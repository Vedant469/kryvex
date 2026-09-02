
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import face_recognition
import numpy as np
import requests
from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError
from solcx import compile_standard, install_solc, set_solc_version
from web3 import Web3


APP_TITLE = "Face ID + Blockchain Verification Pipeline"
SOLIDITY_VERSION = "0.8.20"
FACE_DISTANCE_THRESHOLD = 0.55
DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
LIVENESS_TIMEOUT_SECONDS = 30
REQUIRED_ENV = ("SERPAPI_KEY", "POLYGON_RPC_URL", "WALLET_PRIVATE_KEY")

CONTRACT_SOURCE = Path(__file__).resolve().parent / "VerificationLog.sol"

SOCIAL_DOMAINS = (
    "instagram.com",
    "facebook.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "reddit.com",
    "linkedin.com",
    "threads.net",
    "youtube.com",
    "pinterest.com",
    "snapchat.com",
)


def log(stage: str, message: str) -> None:
    print(f"[{stage}] {message}")


def log_success(message: str) -> None:
    print(f"  ✓ {message}")


def exit_with_error(message: str, code: int = 1) -> None:
    print(f"  ✗ {message}", file=sys.stderr)
    raise SystemExit(code)


def load_config() -> dict[str, str]:
    load_dotenv()

    missing = [name for name in REQUIRED_ENV if not os.getenv(name, "").strip()]
    if missing:
        exit_with_error(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in the values."
        )

    config = {name: os.getenv(name, "").strip() for name in REQUIRED_ENV}
    config["CONTRACT_ADDRESS"] = os.getenv("CONTRACT_ADDRESS", "").strip()
    return config


# ---------------------------------------------------------------------------
# Face processing
# ---------------------------------------------------------------------------

def load_image_rgb(image_path: str) -> np.ndarray:
    path = Path(image_path)
    if not path.is_file():
        exit_with_error(f"Input image not found: {path}")

    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            return np.asarray(image)
    except (UnidentifiedImageError, OSError) as exc:
        exit_with_error(f"Could not decode input image: {exc}")


def _detect_faces_static(image_rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Detect faces in static images using fast HOG with CNN fallback."""
    locations = face_recognition.face_locations(
        image_rgb,
        model="hog",
        number_of_times_to_upsample=1,
    )

    if locations:
        return locations

    log("FACE", "HOG detector found no face; trying CNN fallback…")
    locations = face_recognition.face_locations(
        image_rgb,
        model="cnn",
        number_of_times_to_upsample=1,
    )

    if locations:
        log_success(f"CNN fallback detected {len(locations)} face(s).")

    return locations


def encode_largest_face(image_rgb: np.ndarray) -> tuple[np.ndarray, int]:
    locations = _detect_faces_static(image_rgb)
    if not locations:
        exit_with_error(
            "No face detected in the image. Use a clear photo with a visible face."
        )

    if len(locations) > 1:
        exit_with_error(
            f"Found {len(locations)} faces in the image. "
            "Use a photo with exactly one visible face."
        )

    encodings = face_recognition.face_encodings(
        image_rgb, known_face_locations=locations
    )
    if not encodings:
        exit_with_error("Face was detected but could not be encoded.")

    return encodings[0], 0


def detect_and_hash_face(image_rgb: np.ndarray) -> tuple[np.ndarray, str]:
    log("FACE", "Detecting faces in image…")
    encoding, _ = encode_largest_face(image_rgb)
    face_hash = hash_encoding(encoding)
    log_success(f"Face encoded — SHA-256 hash: {face_hash}")
    return encoding, face_hash


def hash_encoding(encoding: np.ndarray) -> str:
    return hashlib.sha256(encoding.tobytes()).hexdigest()


def face_distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(face_recognition.face_distance([a], b)[0])


def encodings_match(a: np.ndarray, b: np.ndarray, label: str) -> float:
    distance = face_distance(a, b)
    log(
        "MATCH",
        f"{label}: distance={distance:.4f} "
        f"(threshold {FACE_DISTANCE_THRESHOLD:.2f})",
    )
    return distance


# ---------------------------------------------------------------------------
# Liveness / anti-spoofing
# ---------------------------------------------------------------------------

def _eye_aspect_ratio(eye: list[tuple[int, int]]) -> float:
    if len(eye) < 6:
        return 1.0

    p = np.asarray(eye, dtype=np.float32)
    vertical_1 = np.linalg.norm(p[1] - p[5])
    vertical_2 = np.linalg.norm(p[2] - p[4])
    horizontal = np.linalg.norm(p[0] - p[3])

    if horizontal <= 1e-6:
        return 1.0

    return float((vertical_1 + vertical_2) / (2.0 * horizontal))


def _frame_sharpness(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def run_liveness_challenge(camera_index: int) -> tuple[np.ndarray, str]:
    log(
        "LIVENESS",
        "Starting webcam challenge — blink twice within "
        f"{LIVENESS_TIMEOUT_SECONDS} seconds.",
    )

    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        exit_with_error(
            f"Could not open webcam device {camera_index}. "
            "Check camera permissions or try --camera 1."
        )

    nonce = hashlib.sha256(
        f"{time.time_ns()}:{os.urandom(16).hex()}".encode()
    ).hexdigest()[:32]

    deadline = time.monotonic() + LIVENESS_TIMEOUT_SECONDS
    blink_count = 0
    eye_closed = False
    last_encoding: np.ndarray | None = None
    sharp_frames = 0

    try:
        while time.monotonic() < deadline:
            ok, frame = capture.read()
            if not ok:
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(rgb, model="hog")

            if len(locations) != 1:
                cv2.putText(
                    frame,
                    "Show exactly one face",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 0, 255),
                    2,
                )
                cv2.imshow("Kryvex Liveness — press Q to cancel", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    exit_with_error("Liveness challenge cancelled.")
                continue

            landmarks = face_recognition.face_landmarks(
                rgb, face_locations=locations
            )
            if not landmarks:
                continue

            landmark = landmarks[0]
            left_eye = landmark.get("left_eye", [])
            right_eye = landmark.get("right_eye", [])

            left_ear = _eye_aspect_ratio(left_eye)
            right_ear = _eye_aspect_ratio(right_eye)
            ear = (left_ear + right_ear) / 2.0

            sharpness = _frame_sharpness(frame)
            if sharpness >= 35.0:
                sharp_frames += 1

            encodings = face_recognition.face_encodings(
                rgb, known_face_locations=locations
            )
            if encodings:
                last_encoding = encodings[0]

            # HOG + EAR blink heuristic. A blink is counted on the
            # closed -> open transition to avoid counting every closed frame.
            if ear < 0.21:
                eye_closed = True
            elif eye_closed:
                blink_count += 1
                eye_closed = False
                log("LIVENESS", f"Blink detected ({blink_count}/2).")

            remaining = max(0, int(deadline - time.monotonic()))
            cv2.putText(
                frame,
                f"Blink twice | {blink_count}/2 | {remaining}s",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (0, 255, 0),
                2,
            )
            cv2.imshow("Kryvex Liveness — press Q to cancel", frame)

            if blink_count >= 2 and last_encoding is not None and sharp_frames >= 3:
                log_success("Liveness challenge passed.")
                return last_encoding, nonce

            if cv2.waitKey(1) & 0xFF == ord("q"):
                exit_with_error("Liveness challenge cancelled.")

    finally:
        capture.release()
        cv2.destroyAllWindows()

    exit_with_error(
        "Liveness challenge timed out. "
        "Make sure your face is visible and blink twice clearly."
    )
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# SerpApi / Google Lens
# ---------------------------------------------------------------------------

def upload_image_to_serpapi(image_path: str, api_key: str) -> str:
    log("SERPAPI", "Uploading image to SerpApi Image API…")

    path = Path(image_path)
    # SerpApi's Image API currently supports JPG/JPEG, PNG and WebP
    # and documents a 500 KB maximum.
    if path.stat().st_size > 500_000:
        exit_with_error(
            f"Input image is {path.stat().st_size} bytes. "
            "SerpApi Image API currently has a 500 KB upload limit. "
            "Resize/compress the image first."
        )

    try:
        with path.open("rb") as fh:
            response = requests.post(
                "https://serpapi.com/image",
                files={"image": (path.name, fh, "application/octet-stream")},
                data={"api_key": api_key},
                timeout=60,
            )
    except requests.RequestException as exc:
        exit_with_error(f"SerpApi image upload failed: {exc}")

    if not response.ok:
        exit_with_error(
            f"SerpApi image upload failed: HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    try:
        payload = response.json()
    except ValueError:
        exit_with_error("SerpApi image upload returned invalid JSON.")

    if payload.get("error"):
        exit_with_error(f"SerpApi image upload error: {payload['error']}")

    image_id = payload.get("image_id")
    if not image_id:
        exit_with_error("SerpApi upload succeeded but no image_id was returned.")

    log_success(f"Image uploaded — image_id: {image_id[:24]}…")
    return str(image_id)


def run_google_lens_search(image_id: str, api_key: str) -> dict[str, Any]:
    log("SERPAPI", "Running Google Lens reverse image search…")

    params = {
        "engine": "google_lens",
        "image_id": image_id,
        "type": "all",
        "api_key": api_key,
        "no_cache": "true",
    }

    try:
        response = requests.get(
            "https://serpapi.com/search.json",
            params=params,
            timeout=90,
        )
    except requests.RequestException as exc:
        exit_with_error(f"Google Lens request failed: {exc}")

    if not response.ok:
        exit_with_error(
            f"Google Lens request failed: HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    try:
        payload = response.json()
    except ValueError:
        exit_with_error("Google Lens returned invalid JSON.")

    if payload.get("error"):
        exit_with_error(f"Google Lens error: {payload['error']}")

    log_success("Google Lens search completed.")
    return payload


def _is_social_media_url(url: str) -> bool:
    lowered = url.lower()
    return any(domain in lowered for domain in SOCIAL_DOMAINS)


def _image_candidate_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith(("http://", "https://")) else []

    if isinstance(value, dict):
        urls: list[str] = []
        for key in (
            "link",
            "url",
            "image",
            "thumbnail",
            "original",
            "original_image",
            "image_url",
            "thumbnail_url",
        ):
            if key in value:
                urls.extend(_image_candidate_urls(value[key]))
        return urls

    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(_image_candidate_urls(item))
        return urls

    return []


def _result_image_candidates(result: dict[str, Any]) -> list[str]:
    urls: list[str] = []

    for key in (
        "image",
        "thumbnail",
        "original",
        "original_image",
        "image_url",
        "thumbnail_url",
    ):
        if key in result:
            urls.extend(_image_candidate_urls(result[key]))

    # Some Lens response objects nest image data.
    for key in ("images", "image_sources", "source", "visual_matches"):
        if key in result:
            urls.extend(_image_candidate_urls(result[key]))

    return list(dict.fromkeys(urls))


def _collect_social_results(
    obj: Any,
    found: list[dict[str, Any]],
) -> None:
    if isinstance(obj, dict):
        link = obj.get("link")
        if isinstance(link, str) and link.startswith(("http://", "https://")):
            if _is_social_media_url(link):
                found.append(
                    {
                        "link": link,
                        "title": str(obj.get("title") or ""),
                        "source": str(obj.get("source") or ""),
                        "image_urls": _result_image_candidates(obj),
                    }
                )

        for value in obj.values():
            _collect_social_results(value, found)

    elif isinstance(obj, list):
        for item in obj:
            _collect_social_results(item, found)


def find_social_media_matches(
    results: dict[str, Any],
) -> list[dict[str, Any]]:
    log("SERPAPI", "Scanning results for social-media matches…")

    candidates: list[dict[str, Any]] = []
    _collect_social_results(results, candidates)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []

    for item in candidates:
        link = item["link"]
        if link in seen:
            continue
        seen.add(link)
        unique.append(item)

    with_photo = [item for item in unique if item.get("image_urls")]
    without_photo = [item for item in unique if not item.get("image_urls")]
    ordered = with_photo + without_photo

    if not ordered:
        exit_with_error(
            "No social-media match found via reverse image search. "
            "Try a photo that appears on a public social profile."
        )

    log_success(f"{len(ordered)} social-media candidates found.")
    return ordered


def download_match_face(
    image_urls: list[str],
) -> tuple[np.ndarray, bytes]:
    if not image_urls:
        raise ValueError("No image candidates attached to this social result.")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/139 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }

    for index, image_url in enumerate(image_urls, start=1):
        label = f"candidate {index}/{len(image_urls)}"

        try:
            response = requests.get(
                image_url,
                headers=headers,
                timeout=30,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            log("MATCH", f"Skipping {label}: request failed ({exc.__class__.__name__}).")
            continue

        if not 200 <= response.status_code < 300:
            log("MATCH", f"Skipping {label}: HTTP {response.status_code}.")
            continue

        content = response.content

        if len(content) > DOWNLOAD_MAX_BYTES:
            log("MATCH", f"Skipping {label}: image exceeds size limit.")
            continue

        content_type = (
            response.headers.get("Content-Type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )

        # Reject obvious HTML/login/challenge responses. Some CDNs return
        # application/octet-stream for actual images, so decoding is the
        # final authority rather than Content-Type alone.
        if content_type.startswith("text/") or "html" in content_type:
            log(
                "MATCH",
                f"Skipping {label}: non-image Content-Type "
                f"({content_type or 'missing'}).",
            )
            continue

        try:
            with Image.open(BytesIO(content)) as image:
                image = image.convert("RGB")
                image_rgb = np.asarray(image)
        except (UnidentifiedImageError, OSError, ValueError):
            log("MATCH", f"Skipping {label}: image bytes could not be decoded.")
            continue

        locations = _detect_faces_static(image_rgb)

        if len(locations) != 1:
            log(
                "MATCH",
                f"Skipping {label}: expected exactly one face, found "
                f"{len(locations)}.",
            )
            continue

        encodings = face_recognition.face_encodings(
            image_rgb,
            known_face_locations=locations,
        )

        if not encodings:
            log("MATCH", f"Skipping {label}: face encoding failed.")
            continue

        log_success(f"Match image face encoded from {label}.")
        return encodings[0], content

    raise ValueError("All image candidates failed download/decode/face detection.")


# ---------------------------------------------------------------------------
# Solidity / Polygon
# ---------------------------------------------------------------------------

def compile_contract() -> tuple[list[dict[str, Any]], str]:
    if not CONTRACT_SOURCE.is_file():
        exit_with_error(
            f"Contract source not found: {CONTRACT_SOURCE}. "
            "Keep VerificationLog.sol beside pipeline.py."
        )

    source = CONTRACT_SOURCE.read_text(encoding="utf-8")

    try:
        install_solc(SOLIDITY_VERSION)
        set_solc_version(SOLIDITY_VERSION)

        compiled = compile_standard(
            {
                "language": "Solidity",
                "sources": {
                    CONTRACT_SOURCE.name: {"content": source}
                },
                "settings": {
    "optimizer": {
        "enabled": True,
        "runs": 200,
    },
    "outputSelection": {
        "*": {
            "*": ["abi", "evm.bytecode.object"]
        }
    }
},
            },
            solc_version=SOLIDITY_VERSION,
        )
    except Exception as exc:
        exit_with_error(f"Solidity compilation failed: {exc}")

    contract_data = compiled["contracts"][CONTRACT_SOURCE.name]["VerificationLog"]
    abi = contract_data["abi"]
    bytecode = contract_data["evm"]["bytecode"]["object"]

    if not bytecode:
        exit_with_error("Compiled contract contains no bytecode.")

    log_success("Contract compiled.")
    return abi, bytecode


def _signed_raw_transaction(signed: Any) -> bytes:
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("web3.py signed transaction has no raw transaction bytes.")
    return raw


def _build_gas_fields(w3: Web3) -> dict[str, int]:
    gas_price = int(w3.eth.gas_price)

    # Polygon Amoy currently requires at least a 25 gwei priority fee.
    min_priority_fee = 25_000_000_000
    max_priority_fee = 30_000_000_000

    priority = min(
        max_priority_fee,
        max(min_priority_fee, gas_price // 10),
    )

    max_fee = max(
        gas_price * 2,
        priority * 2,
    )

    return {
        "maxPriorityFeePerGas": priority,
        "maxFeePerGas": max_fee,
    }


def connect_web3(rpc_url: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))

    if not w3.is_connected():
        exit_with_error("Could not connect to Polygon RPC.")

    chain_id = int(w3.eth.chain_id)
    log_success(f"RPC connected — chain ID: {chain_id}")

    if chain_id != 80002:
        exit_with_error(
            f"Expected Polygon Amoy chain ID 80002, but RPC returned {chain_id}."
        )

    return w3


def get_contract(
    w3: Web3,
    config: dict[str, str],
    abi: list[dict[str, Any]],
    bytecode: str,
) -> tuple[Any, Any]:
    try:
        account = w3.eth.account.from_key(config["WALLET_PRIVATE_KEY"])
    except Exception as exc:
        exit_with_error(
            "WALLET_PRIVATE_KEY is not a valid 64-hex-character private key."
        )

    balance = w3.eth.get_balance(account.address)
    log("BLOCKCHAIN", f"Wallet: {account.address}")
    log(
        "BLOCKCHAIN",
        f"Balance: {w3.from_wei(balance, 'ether')} POL",
    )

    if balance == 0:
        exit_with_error(
            "Wallet has 0 POL on Polygon Amoy. Fund it with testnet POL before "
            "deploying/submitting transactions."
        )

    contract_address = config.get("CONTRACT_ADDRESS", "")

    if contract_address:
        if not w3.is_address(contract_address):
            exit_with_error(f"Invalid CONTRACT_ADDRESS: {contract_address}")

        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=abi,
        )
        log_success(f"Using existing contract: {contract_address}")
        return contract, account

    log("BLOCKCHAIN", "Deploying VerificationLog contract to Amoy…")

    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    nonce = w3.eth.get_transaction_count(account.address)

    tx = Contract.constructor().build_transaction(
    {
        "from": account.address,
        "nonce": nonce,
        "chainId": 80002,
        "gas": 1_000_000,
        **_build_gas_fields(w3),
    }
)

    try:
       # Polygon Amoy can occasionally fail gas estimation for contract
       # creation even when the deployment itself is valid.
       # VerificationLog is a small contract, so use an explicit safe limit.
       tx["gas"] = 1_000_000

       signed = account.sign_transaction(tx)
       tx_hash = w3.eth.send_raw_transaction(_signed_raw_transaction(signed))
       log("BLOCKCHAIN", f"Deploy tx sent: {tx_hash.hex()}")

       receipt = w3.eth.wait_for_transaction_receipt(
            tx_hash,
            timeout=180,
        )
    except Exception as exc:
        exit_with_error(f"Contract deployment failed: {exc}")

    if receipt["status"] != 1 or not receipt.get("contractAddress"):
        exit_with_error(
            f"Contract deployment failed. Tx: {tx_hash.hex()}"
        )

    contract_address = receipt["contractAddress"]
    log_success(f"Contract deployed at: {contract_address}")

    print(
        f"    PolygonScan: "
        f"https://amoy.polygonscan.com/address/{contract_address}"
    )

    return (
        w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=abi,
        ),
        account,
    )


def submit_record(
    w3: Web3,
    contract: Any,
    account: Any,
    face_hash: str,
    matched_url: str,
    post_hash: str,
    timestamp: int,
    challenge_nonce: str,
) -> str:
    log("BLOCKCHAIN", "Writing verification record to Polygon Amoy…")

    nonce = w3.eth.get_transaction_count(account.address)

    tx = contract.functions.addRecord(
        face_hash,
        matched_url,
        post_hash,
        timestamp,
        challenge_nonce,
    ).build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "chainId": 80002,
            **_build_gas_fields(w3),
        }
    )

    try:
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(_signed_raw_transaction(signed))
        log("BLOCKCHAIN", f"Transaction sent: {tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(
            tx_hash,
            timeout=180,
        )
    except Exception as exc:
        exit_with_error(f"Blockchain transaction failed: {exc}")

    if receipt["status"] != 1:
        exit_with_error(f"Transaction reverted. Tx: {tx_hash.hex()}")

    log_success(f"Verification record confirmed in block {receipt['blockNumber']}.")
    print(f"    Transaction: https://amoy.polygonscan.com/tx/{tx_hash.hex()}")

    return tx_hash.hex()


def verify_record_on_chain(
    contract: Any,
    face_hash: str,
    matched_url: str,
    post_hash: str,
    timestamp: int,
) -> bool:
    """Read the latest record and verify all fingerprint fields."""
    try:
        count = int(contract.functions.recordCount().call())
        if count <= 0:
            return False

        record = contract.functions.getRecord(count - 1).call()

        # Expected struct order from VerificationLog.sol:
        # faceHash, matchedUrl, postHash, timestamp, nonce
        chain_face_hash = str(record[0])
        chain_url = str(record[1])
        chain_post_hash = str(record[2])
        chain_timestamp = int(record[3])

        face_verified = chain_face_hash == face_hash
        url_verified = chain_url == matched_url
        post_verified = chain_post_hash == post_hash
        timestamp_verified = chain_timestamp == timestamp

        log("VERIFY", f"Face hash: {'✓' if face_verified else '✗'}")
        log("VERIFY", f"Social URL: {'✓' if url_verified else '✗'}")
        log("VERIFY", f"Post fingerprint: {'✓' if post_verified else '✗'}")
        log("VERIFY", f"Timestamp: {'✓' if timestamp_verified else '✗'}")

        return (
            face_verified
            and url_verified
            and post_verified
            and timestamp_verified
        )
    except Exception as exc:
        log("BLOCKCHAIN", f"On-chain readback failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=APP_TITLE,
    )

    parser.add_argument(
        "image",
        help="Path to the input image containing one face",
    )

    parser.add_argument(
        "--dump-results",
        action="store_true",
        help="Save raw SerpApi JSON to serpapi_results.json",
    )

    parser.add_argument(
        "--skip-liveness",
        action="store_true",
        help="Skip webcam liveness challenge (debug only)",
    )

    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="Webcam device index (default: 0)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()

    print("=" * 60)
    print(f"  {APP_TITLE}")
    print("=" * 60)
    print()

    # Step 1 — uploaded face
    image_rgb = load_image_rgb(args.image)
    uploaded_encoding, uploaded_hash = detect_and_hash_face(image_rgb)
    print()

    # Step 1b — liveness
    if args.skip_liveness:
        log(
            "LIVENESS",
            "SKIPPED — photo-to-camera spoofing is not blocked.",
        )
        live_encoding = uploaded_encoding
        challenge_nonce = "SKIPPED"
    else:
        live_encoding, challenge_nonce = run_liveness_challenge(args.camera)

        distance = encodings_match(
            uploaded_encoding,
            live_encoding,
            "live webcam vs uploaded photo",
        )

        if distance > FACE_DISTANCE_THRESHOLD:
            exit_with_error(
                f"Live person does not match uploaded photo. "
                f"Distance {distance:.4f} > {FACE_DISTANCE_THRESHOLD:.2f}."
            )

        log_success("Live webcam matches uploaded face.")

    print()

    # Step 2 — genuine web/social-media discovery
    image_id = upload_image_to_serpapi(
        args.image,
        config["SERPAPI_KEY"],
    )

    lens_results = run_google_lens_search(
        image_id,
        config["SERPAPI_KEY"],
    )

    if args.dump_results:
        dump_path = Path("serpapi_results.json")
        dump_path.write_text(
            json.dumps(lens_results, indent=2),
            encoding="utf-8",
        )
        log("SERPAPI", f"Raw results saved to {dump_path}")

    matches = find_social_media_matches(lens_results)

    print()
    log("MATCH", f"Testing up to {len(matches)} social-media results...")

    matched_url: str | None = None
    matched_distance: float | None = None

    best_url: str | None = None
    best_distance: float | None = None
    best_image_bytes: bytes | None = None

    for result_index, match in enumerate(matches, start=1):
        print()
        log(
            "MATCH",
            f"Testing social result {result_index}/{len(matches)}",
        )
        print(f"    URL   : {match['link']}")

        if match.get("title"):
            print(f"    Title : {match['title']}")

        if match.get("source"):
            print(f"    Source: {match['source']}")

        try:
            match_encoding, match_image_bytes = download_match_face(
                match.get("image_urls", [])
            )
        except ValueError as exc:
            log("MATCH", f"Skipping result: {exc}")
            continue

        distance = encodings_match(
            live_encoding,
            match_encoding,
            "live webcam vs Google Lens match photo",
        )

        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_url = match["link"]
            best_image_bytes = match_image_bytes
            log(
                "MATCH",
                f"New best candidate — distance={distance:.4f}",
            )

        if distance <= FACE_DISTANCE_THRESHOLD:
            log_success(
                f"Candidate passes threshold — distance={distance:.4f}. "
                "Continuing to verify remaining candidates."
            )

    if (
        best_url is not None
        and best_distance is not None
        and best_distance <= FACE_DISTANCE_THRESHOLD
    ):
        matched_url = best_url
        matched_distance = best_distance

        log_success(
            f"BEST BIOMETRIC MATCH — distance={matched_distance:.4f}."
        )
    else:
        exit_with_error(
            "No social-media result passed biometric verification. "
            "All usable Lens candidates were tested."
        )

    print()
    print(f"    Verified social URL : {matched_url}")
    print(f"    Face distance       : {matched_distance:.4f}")

    # Step 3 — blockchain
    timestamp = int(time.time())

    # Store the live face encoding hash, not the raw biometric image.
    face_hash = hash_encoding(live_encoding)

    if best_image_bytes is None or matched_url is None:
        exit_with_error("Cannot fingerprint the verified social post.")

    # Fingerprint the verified social evidence using the matched URL
    # together with the exact downloaded image bytes that were face-matched.
    post_hash = hashlib.sha256(
        matched_url.encode("utf-8") + best_image_bytes
    ).hexdigest()

    log("POST", f"Post fingerprint (SHA-256): {post_hash}")
    log("FACE", f"On-chain face hash: {face_hash}")
    print(f"    Uploaded-photo hash (audit only): {uploaded_hash}")
    print()

    abi, bytecode = compile_contract()
    w3 = connect_web3(config["POLYGON_RPC_URL"])
    contract, account = get_contract(
        w3,
        config,
        abi,
        bytecode,
    )

    tx_hash = submit_record(
        w3,
        contract,
        account,
        face_hash,
        matched_url,
        post_hash,
        timestamp,
        challenge_nonce,
    )

    print()

    # Demonstrate the required "re-verifying against the on-chain record".
    log("VERIFY", "Reading the latest verification record from Polygon…")

    verified = verify_record_on_chain(
        contract,
        face_hash,
        matched_url,
        post_hash,
        timestamp,
    )

    if not verified:
        exit_with_error(
            "On-chain verification failed: the stored record did not "
            "match the submitted fingerprints."
        )

    log_success("POST FINGERPRINT VERIFIED.")
    log_success("ON-CHAIN VERIFICATION PASSED.")
    print()
    print("=" * 60)
    print("  END-TO-END VERIFICATION COMPLETE")
    print("=" * 60)
    print(f"  Social match : {matched_url}")
    print(f"  Face hash    : {face_hash}")
    print(f"  Post hash    : {post_hash}")
    print(f"  Tx hash      : {tx_hash}")
    print("=" * 60)


if __name__ == "__main__":
    main()
