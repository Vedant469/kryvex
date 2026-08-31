#!/usr/bin/env python3
"""
Face ID + Blockchain Verification Pipeline

Detects a face, reverse-searches it via SerpApi Google Lens,
and records the match on Polygon Amoy testnet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import cv2
import face_recognition
import numpy as np
import requests
from dotenv import load_dotenv
from PIL import Image
from serpapi import GoogleSearch
from solcx import compile_standard, install_solc, set_solc_version
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOCIAL_MEDIA_DOMAINS: tuple[str, ...] = (
    "facebook.com",
    "fb.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
    "threads.net",
    "snapchat.com",
    "youtube.com",
)

SERPAPI_IMAGE_MAX_BYTES = 500 * 1024  # 500 KB SerpApi upload limit
AMOY_CHAIN_ID = 80002
POLYGONSCAN_TX_URL = "https://amoy.polygonscan.com/tx/{tx_hash}"
SOLC_VERSION = "0.8.20"
CONTRACT_SOURCE = Path(__file__).resolve().parent / "VerificationLog.sol"

# Face match: lower = stricter. 0.6 is face_recognition's default "same person" cutoff.
FACE_DISTANCE_THRESHOLD = 0.55
EAR_BLINK_THRESHOLD = 0.21
BLINK_CONSEC_FRAMES = 2
BLINKS_REQUIRED = 2
LIVENESS_TIMEOUT_SEC = 30
MIN_FACE_SHARPNESS = 40.0
DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def log(step: str, message: str) -> None:
    print(f"[{step}] {message}")


def log_success(message: str) -> None:
    print(f"  ✓ {message}")


def log_error(message: str) -> None:
    print(f"  ✗ {message}", file=sys.stderr)


def exit_with_error(message: str, code: int = 1) -> None:
    log_error(message)
    sys.exit(code)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def load_config() -> dict[str, str]:
    load_dotenv()

    required = ("SERPAPI_KEY", "POLYGON_RPC_URL", "WALLET_PRIVATE_KEY")
    config: dict[str, str] = {}
    missing: list[str] = []

    for key in required:
        value = os.getenv(key, "").strip()
        if not value:
            missing.append(key)
        else:
            config[key] = value

    if missing:
        exit_with_error(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in your keys."
        )

    config["CONTRACT_ADDRESS"] = os.getenv("CONTRACT_ADDRESS", "").strip()
    return config


# ---------------------------------------------------------------------------
# Step 1 — Face detection & encoding
# ---------------------------------------------------------------------------


def load_image_rgb(image_path: Path) -> np.ndarray:
    if not image_path.is_file():
        exit_with_error(f"Image not found: {image_path}")

    image_bgr = cv2.imread(str(image_path))
    if image_bgr is None:
        exit_with_error(f"Unable to read image (unsupported or corrupt): {image_path}")

    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)


def detect_and_hash_face(image_rgb: np.ndarray) -> tuple[np.ndarray, str]:
    log("FACE", "Detecting faces in image…")
    face_locations = face_recognition.face_locations(image_rgb, model="hog")

    if not face_locations:
        exit_with_error(
            "No face detected in the image. "
            "Use a clear, front-facing photo with a single visible face."
        )

    if len(face_locations) > 1:
        exit_with_error(
            f"Found {len(face_locations)} faces in the image. "
            "Use a photo with exactly one face (rejects 'hold a photo next to me')."
        )

    # Pick the face with the largest bounding box
    def area(loc: tuple[int, int, int, int]) -> int:
        top, right, bottom, left = loc
        return (bottom - top) * (right - left)

    best_location = max(face_locations, key=area)
    encodings = face_recognition.face_encodings(image_rgb, known_face_locations=[best_location])

    if not encodings:
        exit_with_error("Face was detected but encoding failed. Try a higher-quality image.")

    encoding = encodings[0]
    face_hash = hash_encoding(encoding)
    log_success(f"Face encoded — SHA-256 hash: {face_hash}")
    return encoding, face_hash


def hash_encoding(encoding: np.ndarray) -> str:
    return hashlib.sha256(encoding.tobytes()).hexdigest()


def encode_largest_face(image_rgb: np.ndarray) -> tuple[np.ndarray, int]:
    """Return (encoding, face_count). encoding is None-equivalent via empty if none."""
    locations = face_recognition.face_locations(image_rgb, model="hog")
    if not locations:
        return np.array([]), 0

    def area(loc: tuple[int, int, int, int]) -> int:
        top, right, bottom, left = loc
        return (bottom - top) * (right - left)

    best = max(locations, key=area)
    encodings = face_recognition.face_encodings(image_rgb, known_face_locations=[best])
    if not encodings:
        return np.array([]), len(locations)
    return encodings[0], len(locations)


def encodings_match(a: np.ndarray, b: np.ndarray, label: str) -> float:
    if a.size == 0 or b.size == 0:
        exit_with_error(f"Cannot compare faces ({label}): missing encoding.")
    distance = float(face_recognition.face_distance([a], b)[0])
    same = distance <= FACE_DISTANCE_THRESHOLD
    log("MATCH", f"{label}: distance={distance:.4f} (threshold {FACE_DISTANCE_THRESHOLD})")
    if not same:
        exit_with_error(
            f"Face mismatch ({label}). Distance {distance:.4f} > {FACE_DISTANCE_THRESHOLD}. "
            "The live person does not match the photo being verified."
        )
    log_success(f"{label}: same person")
    return distance


# ---------------------------------------------------------------------------
# Step 2 — SerpApi reverse image search (Google Lens)
# ---------------------------------------------------------------------------


def prepare_image_for_upload(image_path: Path) -> tuple[bytes, str]:
    """Resize/compress image to stay within SerpApi's 500 KB upload limit."""
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        quality = 90
        max_dim = 2048

        while True:
            working = img.copy()
            w, h = working.size
            if max(w, h) > max_dim:
                scale = max_dim / max(w, h)
                working = working.resize(
                    (int(w * scale), int(h * scale)), Image.Resampling.LANCZOS
                )

            buffer = BytesIO()
            working.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = buffer.getvalue()

            if len(data) <= SERPAPI_IMAGE_MAX_BYTES:
                return data, "image/jpeg"

            if quality > 40:
                quality -= 10
            elif max_dim > 512:
                max_dim = int(max_dim * 0.75)
            else:
                exit_with_error(
                    f"Unable to compress image below {SERPAPI_IMAGE_MAX_BYTES} bytes "
                    "for SerpApi upload."
                )


def upload_image_to_serpapi(image_path: Path, api_key: str) -> str:
    log("SERPAPI", "Uploading image to SerpApi Image API…")
    image_bytes, mime_type = prepare_image_for_upload(image_path)

    response = requests.post(
        "https://serpapi.com/image",
        params={"api_key": api_key},
        files={"image": ("upload.jpg", image_bytes, mime_type)},
        timeout=60,
    )

    if response.status_code != 200:
        exit_with_error(
            f"SerpApi image upload failed ({response.status_code}): {response.text}"
        )

    payload = response.json()
    image_id = payload.get("image_id")
    if not image_id:
        exit_with_error(f"SerpApi did not return an image_id: {payload}")

    log_success(f"Image uploaded — image_id: {image_id[:24]}…")
    return image_id


def run_google_lens_search(image_id: str, api_key: str) -> dict[str, Any]:
    log("SERPAPI", "Running Google Lens reverse image search…")

    params = {
        "engine": "google_lens",
        "image_id": image_id,
        "api_key": api_key,
        "hl": "en",
        "type": "all",
    }

    try:
        results = GoogleSearch(params).get_dict()
    except Exception as exc:
        exit_with_error(f"SerpApi Google Lens request failed: {exc}")

    if "error" in results:
        exit_with_error(f"SerpApi returned an error: {results['error']}")

    log_success("Google Lens search completed.")
    return results


def _is_social_media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return any(host == domain or host.endswith(f".{domain}") for domain in SOCIAL_MEDIA_DOMAINS)
    except Exception:
        return False


def _collect_links(obj: Any, found: list[dict[str, str]]) -> None:
    """Recursively harvest link/title/source tuples from SerpApi JSON."""
    if isinstance(obj, dict):
        link = obj.get("link")
        if isinstance(link, str) and link.startswith("http"):
            raw_image = obj.get("image") or obj.get("thumbnail") or ""
            if isinstance(raw_image, dict):
                raw_image = raw_image.get("link") or raw_image.get("url") or ""
            found.append(
                {
                    "link": link,
                    "title": str(obj.get("title", "")),
                    "source": str(obj.get("source", "")),
                    "image": str(raw_image) if raw_image else "",
                }
            )
        for value in obj.values():
            _collect_links(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _collect_links(item, found)


def find_social_media_match(results: dict[str, Any]) -> dict[str, str]:
    log("SERPAPI", "Scanning results for social-media matches…")

    candidates: list[dict[str, str]] = []
    _collect_links(results, candidates)

    # De-duplicate while preserving order
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for item in candidates:
        link = item["link"]
        if link not in seen:
            seen.add(link)
            unique.append(item)

    social_matches = [item for item in unique if _is_social_media_url(item["link"])]

    if not social_matches:
        log("SERPAPI", f"Total links found: {len(unique)} (none on social media).")
        if unique:
            log("SERPAPI", "Sample non-social links:")
            for sample in unique[:5]:
                print(f"    - {sample['link']}")
        exit_with_error(
            "No social-media match found via reverse image search. "
            "Try a photo that appears on a public social profile."
        )

    with_photo = [item for item in social_matches if item.get("image")]
    match = with_photo[0] if with_photo else social_matches[0]
    log_success(f"Social-media match: {match['link']}")
    if match.get("title"):
        print(f"    Title : {match['title']}")
    if match.get("source"):
        print(f"    Source: {match['source']}")
    if len(social_matches) > 1:
        print(f"    ({len(social_matches)} social matches found — using the first.)")

    return match


def download_match_face(image_url: str) -> np.ndarray:
    log("MATCH", f"Downloading Lens match image for biometric compare…")
    if not image_url:
        exit_with_error(
            "Social match has no thumbnail/image URL. Cannot compare faces. "
            "Try another photo or run with --dump-results to inspect SerpApi JSON."
        )

    headers = {
        "User-Agent": "Mozilla/5.0 (FaceID-Verification/1.0)",
        "Accept": "image/*,*/*;q=0.8",
    }
    try:
        response = requests.get(image_url, headers=headers, timeout=30, stream=True)
    except Exception as exc:
        exit_with_error(f"Failed to download match image: {exc}")

    if response.status_code != 200:
        exit_with_error(
            f"Match image download failed ({response.status_code}): {image_url}"
        )

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > DOWNLOAD_MAX_BYTES:
            exit_with_error("Match image exceeded size limit.")
        chunks.append(chunk)

    encoded = np.frombuffer(b"".join(chunks), dtype=np.uint8)
    bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if bgr is None:
        exit_with_error("Match image could not be decoded as a picture.")

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    encoding, count = encode_largest_face(rgb)
    if count == 0 or encoding.size == 0:
        exit_with_error("No face found in the reverse-search match image.")
    log_success("Match image face encoded.")
    return encoding


# ---------------------------------------------------------------------------
# Step 2b — Webcam liveness (blink + session nonce)
# ---------------------------------------------------------------------------


def _eye_aspect_ratio(eye_points: list) -> float:
    pts = np.array(eye_points, dtype=np.float64)
    if pts.shape[0] < 6:
        return 0.0
    vertical = np.linalg.norm(pts[1] - pts[5]) + np.linalg.norm(pts[2] - pts[4])
    horizontal = np.linalg.norm(pts[0] - pts[3])
    if horizontal == 0:
        return 0.0
    return float(vertical / (2.0 * horizontal))


def _face_sharpness(image_rgb: np.ndarray, location: tuple[int, int, int, int]) -> float:
    top, right, bottom, left = location
    crop = image_rgb[max(top, 0) : bottom, max(left, 0) : right]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def run_liveness_challenge(camera_index: int) -> tuple[np.ndarray, str]:
    """Require a live blink in front of the webcam. Returns (live_encoding, nonce)."""
    nonce = secrets.token_hex(4).upper()
    log("LIVENESS", "Starting webcam challenge — blink twice while looking at the camera.")
    print(f"    Challenge nonce: {nonce}")
    print(f"    Window timeout : {LIVENESS_TIMEOUT_SEC}s  |  Press Q to abort")

    if sys.platform.startswith("win"):
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(camera_index)

    if not cap.isOpened():
        exit_with_error(
            f"Cannot open webcam (index {camera_index}). "
            "Plug in a camera or pass --skip-liveness only for debugging."
        )

    blinks = 0
    closed_frames = 0
    eyes_were_open = False
    live_encoding: np.ndarray | None = None
    show_window = True
    deadline = time.time() + LIVENESS_TIMEOUT_SEC

    try:
        while time.time() < deadline:
            ok, frame_bgr = cap.read()
            if not ok:
                continue

            frame_bgr = cv2.flip(frame_bgr, 1)
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(frame_rgb, model="hog")

            remaining = max(0, int(deadline - time.time()))
            status = f"Nonce {nonce} | Blink {blinks}/{BLINKS_REQUIRED} | {remaining}s"

            if len(locations) > 1:
                status = "Multiple faces — one person only"
            elif not locations:
                status = f"No face | Nonce {nonce} | {remaining}s"
            else:
                loc = locations[0]
                landmarks_list = face_recognition.face_landmarks(
                    frame_rgb, face_locations=[loc]
                )
                sharpness = _face_sharpness(frame_rgb, loc)
                top, right, bottom, left = loc
                cv2.rectangle(frame_bgr, (left, top), (right, bottom), (0, 220, 0), 2)

                if sharpness < MIN_FACE_SHARPNESS:
                    status = f"Hold still / move closer (blur or screen?) | {remaining}s"
                elif landmarks_list:
                    lm = landmarks_list[0]
                    left_ear = _eye_aspect_ratio(lm.get("left_eye", []))
                    right_ear = _eye_aspect_ratio(lm.get("right_eye", []))
                    ear = (left_ear + right_ear) / 2.0

                    if ear < EAR_BLINK_THRESHOLD:
                        closed_frames += 1
                    else:
                        if closed_frames >= BLINK_CONSEC_FRAMES and eyes_were_open:
                            blinks += 1
                            log("LIVENESS", f"Blink {blinks}/{BLINKS_REQUIRED} detected")
                        closed_frames = 0
                        eyes_were_open = True

                    encodings = face_recognition.face_encodings(
                        frame_rgb, known_face_locations=[loc]
                    )
                    if encodings:
                        live_encoding = encodings[0]

                    status = (
                        f"Nonce {nonce} | Blink {blinks}/{BLINKS_REQUIRED} | "
                        f"EAR {ear:.2f} | {remaining}s"
                    )

            cv2.putText(
                frame_bgr,
                status,
                (16, 32),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame_bgr,
                "Blink twice naturally. Printed photos will fail.",
                (16, 62),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

            if show_window:
                try:
                    cv2.imshow("Liveness challenge", frame_bgr)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), ord("Q"), 27):
                        exit_with_error("Liveness aborted by user.")
                except cv2.error:
                    show_window = False

            if blinks >= BLINKS_REQUIRED and live_encoding is not None:
                log_success("Liveness passed (blink challenge + live encoding).")
                return live_encoding, nonce
    finally:
        cap.release()
        if show_window:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass

    exit_with_error(
        "Liveness failed: not enough blinks in time, or no usable live face. "
        "Use a real camera (a printed photo will not blink)."
    )
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Step 3 — Blockchain (Polygon Amoy)
# ---------------------------------------------------------------------------


def compile_contract() -> tuple[list[dict], str]:
    log("BLOCKCHAIN", f"Compiling VerificationLog.sol (solc {SOLC_VERSION})…")
    if not CONTRACT_SOURCE.is_file():
        exit_with_error(f"Contract source not found: {CONTRACT_SOURCE}")

    install_solc(SOLC_VERSION)
    set_solc_version(SOLC_VERSION)

    source = CONTRACT_SOURCE.read_text(encoding="utf-8")
    compiled = compile_standard(
        {
            "language": "Solidity",
            "sources": {"VerificationLog.sol": {"content": source}},
            "settings": {
                "outputSelection": {
                    "*": {"*": ["abi", "evm.bytecode"]},
                }
            },
        },
        solc_version=SOLC_VERSION,
    )

    contract_data = compiled["contracts"]["VerificationLog.sol"]["VerificationLog"]
    abi = contract_data["abi"]
    bytecode = contract_data["evm"]["bytecode"]["object"]
    log_success("Contract compiled.")
    return abi, bytecode


def connect_web3(rpc_url: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))
    # Polygon PoS sidechain uses extraData field differently
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    if not w3.is_connected():
        exit_with_error(f"Cannot connect to Polygon RPC: {rpc_url}")

    chain_id = w3.eth.chain_id
    if chain_id != AMOY_CHAIN_ID:
        exit_with_error(
            f"Connected chain ID {chain_id} != expected Amoy testnet ({AMOY_CHAIN_ID}). "
            "Check POLYGON_RPC_URL."
        )

    return w3


def get_contract(w3: Web3, config: dict[str, str], abi: list[dict], bytecode: str):
    account = w3.eth.account.from_key(config["WALLET_PRIVATE_KEY"])
    log("BLOCKCHAIN", f"Wallet address: {account.address}")

    balance_wei = w3.eth.get_balance(account.address)
    balance_matic = w3.from_wei(balance_wei, "ether")
    log("BLOCKCHAIN", f"Wallet balance: {balance_matic:.6f} MATIC")

    if balance_wei == 0:
        exit_with_error(
            "Wallet has 0 MATIC. Fund it via the Polygon Amoy faucet: "
            "https://faucet.polygon.technology/"
        )

    contract_address = config.get("CONTRACT_ADDRESS", "")
    if contract_address:
        log("BLOCKCHAIN", f"Using existing contract: {contract_address}")
        if not w3.is_address(contract_address):
            exit_with_error(f"Invalid CONTRACT_ADDRESS: {contract_address}")
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_address), abi=abi
        )
    else:
        log("BLOCKCHAIN", "Deploying VerificationLog contract to Amoy…")
        Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
        nonce = w3.eth.get_transaction_count(account.address)

        deploy_tx = Contract.constructor().build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": AMOY_CHAIN_ID,
                "gas": 2_000_000,
                "maxFeePerGas": w3.to_wei(50, "gwei"),
                "maxPriorityFeePerGas": w3.to_wei(30, "gwei"),
            }
        )

        signed = account.sign_transaction(deploy_tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        log("BLOCKCHAIN", f"Deploy tx sent: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

        if receipt["status"] != 1:
            exit_with_error(f"Contract deployment failed. Tx: {tx_hash.hex()}")

        contract_address = receipt["contractAddress"]
        log_success(f"Contract deployed at: {contract_address}")
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_address), abi=abi
        )

    return contract, account


def submit_record(
    w3: Web3,
    contract,
    account,
    face_hash: str,
    matched_url: str,
    timestamp: int,
    challenge_nonce: str,
) -> str:
    log("BLOCKCHAIN", "Submitting verification record on-chain…")
    print(f"    faceHash   : {face_hash}")
    print(f"    matchedUrl : {matched_url}")
    print(f"    timestamp  : {timestamp}")
    print(f"    nonce      : {challenge_nonce}")

    nonce = w3.eth.get_transaction_count(account.address)
    tx = contract.functions.addRecord(
        face_hash, matched_url, timestamp, challenge_nonce
    ).build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "chainId": AMOY_CHAIN_ID,
            "gas": 500_000,
            "maxFeePerGas": w3.to_wei(50, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(30, "gwei"),
        }
    )

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    log("BLOCKCHAIN", f"Transaction sent: {tx_hash.hex()}")
    log("BLOCKCHAIN", "Waiting for confirmation…")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt["status"] != 1:
        exit_with_error(f"Transaction reverted. Tx: {tx_hash.hex()}")

    log_success(f"Confirmed in block {receipt['blockNumber']}")
    return tx_hash.hex()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Face ID + Blockchain Verification Pipeline"
    )
    parser.add_argument(
        "image",
        type=Path,
        help="Path to the input image containing a face",
    )
    parser.add_argument(
        "--dump-results",
        action="store_true",
        help="Save raw SerpApi JSON to serpapi_results.json (debug)",
    )
    parser.add_argument(
        "--skip-liveness",
        action="store_true",
        help="Skip webcam blink challenge (debug only — photo spoofing will succeed)",
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="Webcam device index (default 0)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()

    print("=" * 60)
    print("  Face ID + Blockchain Verification Pipeline")
    print("=" * 60)
    print()

    # --- Step 1: Face on the input photo (used for reverse search) ---
    image_rgb = load_image_rgb(args.image)
    uploaded_encoding, uploaded_hash = detect_and_hash_face(image_rgb)
    print()

    # --- Step 1b: Live person must blink in front of the camera ---
    if args.skip_liveness:
        log("LIVENESS", "SKIPPED — photo-to-camera spoofing is not blocked.")
        live_encoding = uploaded_encoding
        challenge_nonce = "SKIPPED"
    else:
        live_encoding, challenge_nonce = run_liveness_challenge(args.camera)
        encodings_match(uploaded_encoding, live_encoding, "live webcam vs uploaded photo")
    print()

    # --- Step 2: Reverse image search on the uploaded file ---
    image_id = upload_image_to_serpapi(args.image, config["SERPAPI_KEY"])
    lens_results = run_google_lens_search(image_id, config["SERPAPI_KEY"])

    if args.dump_results:
        dump_path = Path("serpapi_results.json")
        dump_path.write_text(json.dumps(lens_results, indent=2), encoding="utf-8")
        log("SERPAPI", f"Raw results saved to {dump_path}")

    match = find_social_media_match(lens_results)
    matched_url = match["link"]
    match_encoding = download_match_face(match.get("image", ""))
    encodings_match(live_encoding, match_encoding, "live webcam vs Google Lens match photo")
    timestamp = int(time.time())
    print()

    # Commit the *live* face, not the file on disk.
    face_hash = hash_encoding(live_encoding)
    log("FACE", f"On-chain face hash is from the live capture: {face_hash}")
    print(f"    Uploaded-photo hash (audit only): {uploaded_hash}")
    print()

    # --- Step 3: Blockchain ---
    abi, bytecode = compile_contract()
    w3 = connect_web3(config["POLYGON_RPC_URL"])
    contract, account = get_contract(w3, config, abi, bytecode)
    tx_hash = submit_record(
        w3, contract, account, face_hash, matched_url, timestamp, challenge_nonce
    )
    print()

    # --- Done ---
    explorer_url = POLYGONSCAN_TX_URL.format(tx_hash=tx_hash)
    print("=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  Face hash     : {face_hash}")
    print(f"  Matched URL   : {matched_url}")
    print(f"  Challenge     : {challenge_nonce}")
    print(f"  Timestamp     : {timestamp}")
    print(f"  PolygonScan   : {explorer_url}")
    print("=" * 60)


if __name__ == "__main__":
    main()
