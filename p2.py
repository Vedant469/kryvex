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

import boto3
import imagehash
import cv2
import face_recognition
import numpy as np
import requests
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError
from solcx import compile_standard, install_solc, set_solc_version
from web3 import Web3


APP_TITLE = "Face ID + Blockchain Verification Pipeline"
SOLIDITY_VERSION = "0.8.20"

# AWS Rekognition CompareFaces is now the source of truth for identity
# matching. dlib/face_recognition is kept only for cheap local face
# detection, blink-based liveness, and the on-chain encoding hash.
LIVE_IDENTITY_THRESHOLD = 90.0
IDENTITY_SIMILARITY_THRESHOLD = 90.0
NEAR_DUPLICATE_SIMILARITY_THRESHOLD = 60.0
PHASH_MAX_DISTANCE = 10
SIMILARITY_MARGIN = 3.0
REKOGNITION_QUALITY_FILTER = "AUTO"

DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
LIVENESS_TIMEOUT_SECONDS = 30
REQUIRED_ENV = ("SERPAPI_KEY", "POLYGON_RPC_URL", "WALLET_PRIVATE_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1"

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
# AWS Rekognition — identity matching
# ---------------------------------------------------------------------------

_rekognition_client = None


def get_rekognition_client():
    global _rekognition_client
    if _rekognition_client is None:
        _rekognition_client = boto3.client("rekognition", region_name=AWS_REGION)
    return _rekognition_client


def compare_faces_aws(
    source_image_bytes: bytes,
    target_image_bytes: bytes,
    label: str,
) -> float:
    """
    Compare two images with AWS Rekognition CompareFaces.
    Returns similarity percentage (0.0 if no match / no face found).
    Rekognition performs its own detection, alignment and embedding —
    no dlib encoding is needed for this comparison.
    """
    client = get_rekognition_client()

    try:
        response = client.compare_faces(
            SourceImage={"Bytes": source_image_bytes},
            TargetImage={"Bytes": target_image_bytes},
            SimilarityThreshold=1.0,  # low floor; we apply our own threshold after
            QualityFilter=REKOGNITION_QUALITY_FILTER,
        )
    except NoCredentialsError:
        exit_with_error(
            "AWS credentials not found. Set AWS_ACCESS_KEY_ID / "
            "AWS_SECRET_ACCESS_KEY (or use an IAM role) before running."
        )
    except (BotoCoreError, ClientError) as exc:
        log("MATCH", f"Rekognition CompareFaces failed for {label}: {exc}")
        return 0.0

    matches = response.get("FaceMatches", [])
    if not matches:
        log("MATCH", f"{label}: no Rekognition face match.")
        return 0.0

    best = max(matches, key=lambda m: m["Similarity"])
    similarity = float(best["Similarity"])
    log("MATCH", f"{label}: similarity={similarity:.2f}%")
    return similarity


# ---------------------------------------------------------------------------
# Local face processing (detection, liveness, on-chain hash only)
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


def read_image_bytes(image_path: str) -> bytes:
    return Path(image_path).read_bytes()


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
        image_rgb, known_face_locations=locations, model="large", num_jitters=3
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


# ---------------------------------------------------------------------------
# Liveness / anti-spoofing (unchanged blink heuristic; still local/dlib)
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


def run_liveness_challenge(camera_index: int) -> tuple[np.ndarray, bytes, str]:
    """
    Returns (live_encoding, live_frame_jpeg_bytes, challenge_nonce).
    live_encoding is a dlib encoding used only for the on-chain hash.
    live_frame_jpeg_bytes is the raw frame handed to AWS Rekognition.
    """
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
    last_frame_bytes: bytes | None = None
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

                # Only keep the best (sharpest) frame with a single, open eye
                # face as the candidate to send to Rekognition.
                encodings = face_recognition.face_encodings(
                    rgb, known_face_locations=locations, model="large", num_jitters=1
                )
                if encodings:
                    ok_encode, buffer = cv2.imencode(".jpg", frame)
                    if ok_encode:
                        last_encoding = encodings[0]
                        last_frame_bytes = buffer.tobytes()

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

            if (
                blink_count >= 2
                and last_encoding is not None
                and last_frame_bytes is not None
                and sharp_frames >= 3
            ):
                log_success("Liveness challenge passed.")
                return last_encoding, last_frame_bytes, nonce

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

def compute_phash(image_bytes: bytes) -> imagehash.ImageHash:
    """Compute a perceptual hash for near-duplicate image detection."""
    with Image.open(BytesIO(image_bytes)) as image:
        return imagehash.phash(image.convert("RGB"))


def upload_bytes_to_serpapi(
    image_bytes: bytes,
    filename: str,
    api_key: str,
) -> str:
    """Upload in-memory image bytes to SerpApi's Image API."""
    if len(image_bytes) > 500_000:
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image = image.convert("RGB")
                quality = 90
                while True:
                    buffer = BytesIO()
                    image.save(buffer, format="JPEG", quality=quality)
                    candidate = buffer.getvalue()
                    if len(candidate) <= 500_000 or quality <= 30:
                        image_bytes = candidate
                        filename = Path(filename).stem + ".jpg"
                        break
                    quality -= 10
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            exit_with_error(f"Could not prepare image for SerpApi: {exc}")

    try:
        response = requests.post(
            "https://serpapi.com/image",
            files={"image": (filename, BytesIO(image_bytes), "image/jpeg")},
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

    return str(image_id)


def extract_social_media_matches(results: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract social-media candidates without failing on an empty result pool."""
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

    return unique


def merge_match_lists(
    *lists: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge candidates from multiple Lens queries by URL."""
    merged: dict[str, dict[str, Any]] = {}

    for match_list in lists:
        for item in match_list:
            link = item["link"]
            if link not in merged:
                merged[link] = dict(item)
                merged[link]["image_urls"] = list(item.get("image_urls", []))
            else:
                merged[link]["image_urls"] = list(
                    dict.fromkeys(
                        merged[link].get("image_urls", [])
                        + item.get("image_urls", [])
                    )
                )

    with_photo = [item for item in merged.values() if item.get("image_urls")]
    without_photo = [item for item in merged.values() if not item.get("image_urls")]
    return with_photo + without_photo


def upload_image_to_serpapi(image_path: str, api_key: str) -> str:
    log("SERPAPI", "Uploading image to SerpApi Image API…")

    path = Path(image_path)
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


def run_google_lens_search(
    image_id: str,
    api_key: str,
    search_type: str = "all",
) -> dict[str, Any]:
    log("SERPAPI", f"Running Google Lens search ({search_type})…")

    # NOTE: removed the unsupported "type": "all" param — it can truncate
    # the visual_matches payload. Default returns the full result set.
    params = {
        "engine": "google_lens",
        "image_id": image_id,
        "type": search_type,
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
        error_text = str(payload["error"])
        if "hasn't returned any results" in error_text.lower():
            log(
                "SERPAPI",
                f"Google Lens {search_type} returned no results; continuing.",
            )
            return {}
        exit_with_error(f"Google Lens error: {error_text}")

    log_success(f"Google Lens search completed ({search_type}).")
    return payload


def _is_generic_social_search_url(url: str) -> bool:
    """Return True for broad directory/search pages, not a specific profile/post."""
    lowered = url.lower()
    generic_patterns = (
        "linkedin.com/pub/dir/",
        "linkedin.com/search/",
        "instagram.com/explore/",
        "facebook.com/search/",
        "reddit.com/search/",
        "tiktok.com/search",
        "youtube.com/results",
    )
    return any(pattern in lowered for pattern in generic_patterns)


def _is_social_media_url(url: str) -> bool:
    lowered = url.lower()
    return any(domain in lowered for domain in SOCIAL_DOMAINS)


def _image_candidate_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith(("http://", "https://")) else []

    if isinstance(value, dict):
        urls: list[str] = []
        # Prefer full-resolution keys before the low-res "thumbnail".
        for key in (
            "original",
            "original_image",
            "image",
            "image_url",
            "url",
            "link",
            "thumbnail",
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
        "original",
        "original_image",
        "image",
        "image_url",
        "thumbnail",
        "thumbnail_url",
    ):
        if key in result:
            urls.extend(_image_candidate_urls(result[key]))

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


def _upscale_if_small(image_rgb: np.ndarray, min_side: int = 400) -> np.ndarray:
    short_side = min(image_rgb.shape[0], image_rgb.shape[1])
    if short_side >= min_side:
        return image_rgb

    scale = min_side / short_side
    new_size = (
        int(image_rgb.shape[1] * scale),
        int(image_rgb.shape[0] * scale),
    )
    return np.asarray(
        Image.fromarray(image_rgb).resize(new_size, Image.LANCZOS)
    )


def download_candidate_image_variants(
    image_urls: list[str],
) -> list[tuple[str, bytes]]:
    """Download all usable candidate images for scoring."""
    if not image_urls:
        raise ValueError("No image candidates attached to this social result.")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/139 Safari/537.36"
        ),
        "Accept": (
            "image/avif,image/webp,image/apng,image/svg+xml,"
            "image/*,*/*;q=0.8"
        ),
    }

    usable: list[tuple[str, bytes]] = []

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
            log(
                "MATCH",
                f"Skipping {label}: request failed ({exc.__class__.__name__}).",
            )
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
        if content_type.startswith("text/") or "html" in content_type:
            log(
                "MATCH",
                f"Skipping {label}: non-image Content-Type "
                f"({content_type or 'missing'}).",
            )
            continue

        try:
            with Image.open(BytesIO(content)) as image:
                image_rgb = np.asarray(image.convert("RGB"))
        except (UnidentifiedImageError, OSError, ValueError):
            log("MATCH", f"Skipping {label}: image bytes could not be decoded.")
            continue

        if not _detect_faces_static(image_rgb):
            log("MATCH", f"Skipping {label}: no detectable face.")
            continue

        image_rgb = _upscale_if_small(image_rgb, min_side=400)
        buffer = BytesIO()
        Image.fromarray(image_rgb).save(buffer, format="JPEG", quality=95)

        usable.append((image_url, buffer.getvalue()))
        log_success(f"Candidate image downloaded from {label}.")

    if not usable:
        raise ValueError(
            "All image candidates failed download/decode/face detection."
        )

    return usable


# ---------------------------------------------------------------------------
# Solidity / Polygon (unchanged)
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
                "sources": {CONTRACT_SOURCE.name: {"content": source}},
                "settings": {
                    "optimizer": {"enabled": True, "runs": 200},
                    "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
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
    min_priority_fee = 25_000_000_000
    max_priority_fee = 30_000_000_000
    priority = min(max_priority_fee, max(min_priority_fee, gas_price // 10))
    max_fee = max(gas_price * 2, priority * 2)
    return {"maxPriorityFeePerGas": priority, "maxFeePerGas": max_fee}


def connect_web3(rpc_url: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))

    if not w3.is_connected():
        exit_with_error("Could not connect to Polygon RPC.")

    chain_id = int(w3.eth.chain_id)
    log_success(f"RPC connected — chain ID: {chain_id}")

    if chain_id != 80002:
        exit_with_error(f"Expected Polygon Amoy chain ID 80002, but RPC returned {chain_id}.")

    return w3


def get_contract(
    w3: Web3, config: dict[str, str], abi: list[dict[str, Any]], bytecode: str
) -> tuple[Any, Any]:
    try:
        account = w3.eth.account.from_key(config["WALLET_PRIVATE_KEY"])
    except Exception:
        exit_with_error("WALLET_PRIVATE_KEY is not a valid 64-hex-character private key.")

    balance = w3.eth.get_balance(account.address)
    log("BLOCKCHAIN", f"Wallet: {account.address}")
    log("BLOCKCHAIN", f"Balance: {w3.from_wei(balance, 'ether')} POL")

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
            address=Web3.to_checksum_address(contract_address), abi=abi
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
        tx["gas"] = 1_000_000
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(_signed_raw_transaction(signed))
        log("BLOCKCHAIN", f"Deploy tx sent: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    except Exception as exc:
        exit_with_error(f"Contract deployment failed: {exc}")

    if receipt["status"] != 1 or not receipt.get("contractAddress"):
        exit_with_error(f"Contract deployment failed. Tx: {tx_hash.hex()}")

    contract_address = receipt["contractAddress"]
    log_success(f"Contract deployed at: {contract_address}")
    print(f"    PolygonScan: https://amoy.polygonscan.com/address/{contract_address}")

    return (
        w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi),
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
        face_hash, matched_url, post_hash, timestamp, challenge_nonce
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
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
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
    challenge_nonce: str,
) -> bool:
    try:
        count = int(contract.functions.recordCount().call())
        if count <= 0:
            return False

        record = contract.functions.getRecord(count - 1).call()

        chain_face_hash = str(record[0])
        chain_url = str(record[1])
        chain_post_hash = str(record[2])
        chain_timestamp = int(record[3])
        chain_nonce = str(record[4])

        face_verified = chain_face_hash == face_hash
        url_verified = chain_url == matched_url
        post_verified = chain_post_hash == post_hash
        timestamp_verified = chain_timestamp == timestamp
        nonce_verified = chain_nonce == challenge_nonce

        log("VERIFY", f"Face hash: {'✓' if face_verified else '✗'}")
        log("VERIFY", f"Social URL: {'✓' if url_verified else '✗'}")
        log("VERIFY", f"Post fingerprint: {'✓' if post_verified else '✗'}")
        log("VERIFY", f"Timestamp: {'✓' if timestamp_verified else '✗'}")
        log("VERIFY", f"Challenge nonce: {'✓' if nonce_verified else '✗'}")

        return (
            face_verified
            and url_verified
            and post_verified
            and timestamp_verified
            and nonce_verified
        )
    except Exception as exc:
        log("BLOCKCHAIN", f"On-chain readback failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("image", help="Path to the input image containing one face")
    parser.add_argument(
        "--dump-results", action="store_true",
        help="Save raw SerpApi JSON to serpapi_results.json",
    )
    parser.add_argument(
        "--skip-liveness", action="store_true",
        help="Skip webcam liveness challenge (debug only)",
    )
    parser.add_argument("--camera", type=int, default=0, help="Webcam device index (default: 0)")
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
    uploaded_image_bytes = read_image_bytes(args.image)
    uploaded_encoding, uploaded_hash = detect_and_hash_face(image_rgb)
    print()

    # Step 1b — liveness + identity match (uploaded photo vs live webcam)
    if args.skip_liveness:
        log("LIVENESS", "SKIPPED — photo-to-camera spoofing is not blocked.")
        live_encoding = uploaded_encoding
        live_frame_bytes = uploaded_image_bytes
        challenge_nonce = "SKIPPED"
    else:
        live_encoding, live_frame_bytes, challenge_nonce = (
            run_liveness_challenge(args.camera)
        )

        live_similarity = compare_faces_aws(
            uploaded_image_bytes,
            live_frame_bytes,
            "live webcam vs uploaded photo",
        )

        log(
            "MATCH",
            f"Live identity similarity: {live_similarity:.2f}% "
            f"(required {LIVE_IDENTITY_THRESHOLD:.1f}%)",
        )

        if live_similarity < LIVE_IDENTITY_THRESHOLD:
            exit_with_error(
                "Live person does not match uploaded photo. "
                f"Similarity {live_similarity:.2f}% < "
                f"{LIVE_IDENTITY_THRESHOLD:.1f}%."
            )

        log_success(
            f"Live webcam matches uploaded face "
            f"(similarity={live_similarity:.2f}%)."
        )

    print()

    # Step 2 — genuine reverse-image discovery.
    # Exact Matches may legitimately return an empty result set. That is
    # not an API failure; Visual Matches remains usable in that case.
    # The pipeline never treats a visual match as proof by itself: AWS
    # Rekognition and the near-duplicate evidence checks are still applied.
    # Use two query images and two Lens modes for better retrieval:
    #   1) uploaded image + exact matches
    #   2) uploaded image + visual matches
    #   3) live frame + exact matches
    #   4) live frame + visual matches
    uploaded_image_id = upload_image_to_serpapi(
        args.image,
        config["SERPAPI_KEY"],
    )

    uploaded_exact_results = run_google_lens_search(
        uploaded_image_id,
        config["SERPAPI_KEY"],
        "exact_matches",
    )
    uploaded_visual_results = run_google_lens_search(
        uploaded_image_id,
        config["SERPAPI_KEY"],
        "visual_matches",
    )

    live_image_id = upload_bytes_to_serpapi(
        live_frame_bytes,
        "live_frame.jpg",
        config["SERPAPI_KEY"],
    )

    live_exact_results = run_google_lens_search(
        live_image_id,
        config["SERPAPI_KEY"],
        "exact_matches",
    )
    live_visual_results = run_google_lens_search(
        live_image_id,
        config["SERPAPI_KEY"],
        "visual_matches",
    )

    if args.dump_results:
        dump_path = Path("serpapi_results.json")
        dump_path.write_text(
            json.dumps(
                {
                    "uploaded_exact_matches": uploaded_exact_results,
                    "uploaded_visual_matches": uploaded_visual_results,
                    "live_exact_matches": live_exact_results,
                    "live_visual_matches": live_visual_results,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log("SERPAPI", f"Raw Lens result sets saved to {dump_path}")

    uploaded_matches = merge_match_lists(
        extract_social_media_matches(uploaded_exact_results),
        extract_social_media_matches(uploaded_visual_results),
    )
    live_matches = merge_match_lists(
        extract_social_media_matches(live_exact_results),
        extract_social_media_matches(live_visual_results),
    )
    matches = merge_match_lists(uploaded_matches, live_matches)

    if not matches:
        exit_with_error(
            "No social-media match found via reverse image search "
            "from either query image."
        )

    # Ignore broad search/directory pages when a specific social profile/post
    # was also returned. This prevents a directory page containing the same
    # image from counting as a second competing identity.
    specific_matches = [
        item for item in matches
        if not _is_generic_social_search_url(item["link"])
    ]
    if specific_matches:
        matches = specific_matches

    log_success(
        f"{len(matches)} unique social-media candidates found "
        "across both Lens queries."
    )

    print()
    log(
        "MATCH",
        f"Testing up to {len(matches)} merged social-media results...",
    )

    uploaded_phash = compute_phash(uploaded_image_bytes)

    # (combined, url, image_bytes, uploaded_sim, live_sim, phash_distance, duplicate)
    candidate_scores: list[
        tuple[float, str, bytes, float, float, int, bool]
    ] = []

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
            variants = download_candidate_image_variants(
                match.get("image_urls", [])
            )
        except ValueError as exc:
            log("MATCH", f"Skipping result: {exc}")
            continue

        for variant_index, (_, candidate_bytes) in enumerate(
            variants,
            start=1,
        ):
            candidate_phash = compute_phash(candidate_bytes)
            phash_distance = uploaded_phash - candidate_phash
            is_near_duplicate = phash_distance <= PHASH_MAX_DISTANCE

            uploaded_similarity = compare_faces_aws(
                uploaded_image_bytes,
                candidate_bytes,
                f"uploaded photo vs social image {variant_index}",
            )
            live_similarity = compare_faces_aws(
                live_frame_bytes,
                candidate_bytes,
                f"live webcam vs social image {variant_index}",
            )

            combined_similarity = (
                0.70 * uploaded_similarity
                + 0.30 * live_similarity
            )

            log(
                "MATCH",
                f"Candidate image {variant_index}: "
                f"uploaded={uploaded_similarity:.2f}%, "
                f"live={live_similarity:.2f}%, "
                f"combined={combined_similarity:.2f}%, "
                f"pHash={phash_distance}, "
                f"near_duplicate={is_near_duplicate}.",
            )

            candidate_scores.append(
                (
                    combined_similarity,
                    match["link"],
                    candidate_bytes,
                    uploaded_similarity,
                    live_similarity,
                    phash_distance,
                    is_near_duplicate,
                )
            )

    if not candidate_scores:
        exit_with_error(
            "No usable social-media candidate images were downloaded "
            "and evaluated."
        )

    # Biometric ranking remains the primary ranking. A near-duplicate is
    # additional evidence, not a replacement for face verification.
    face_ranked = sorted(
        candidate_scores,
        key=lambda item: item[0],
        reverse=True,
    )

    # First look for a candidate that passes the exact-photo evidence path.
    duplicate_candidates = [
        item for item in candidate_scores
        if (
            item[6]
            and item[3] >= NEAR_DUPLICATE_SIMILARITY_THRESHOLD
            and item[4] >= NEAR_DUPLICATE_SIMILARITY_THRESHOLD
        )
    ]

    high_confidence_candidates = [
        item for item in candidate_scores
        if (
            item[3] >= IDENTITY_SIMILARITY_THRESHOLD
            and item[4] >= IDENTITY_SIMILARITY_THRESHOLD
            and item[0] >= IDENTITY_SIMILARITY_THRESHOLD
        )
    ]

    # Prefer a genuine near-duplicate with supporting face evidence because
    # this most directly satisfies the "real match found online" requirement.
    if duplicate_candidates:
        duplicate_candidates.sort(
            key=lambda item: (
                item[5],   # smaller pHash distance is better
                -item[0],  # then stronger combined face similarity
            )
        )
        selected = duplicate_candidates[0]
        acceptance_path = "near_duplicate"
    elif high_confidence_candidates:
        high_confidence_candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )
        selected = high_confidence_candidates[0]
        acceptance_path = "face"
    else:
        selected = face_ranked[0]
        acceptance_path = "none"

    (
        best_combined,
        best_url,
        best_image_bytes,
        best_uploaded_similarity,
        best_live_similarity,
        best_phash_distance,
        best_is_near_duplicate,
    ) = selected

    second_best_similarity = (
        face_ranked[1][0]
        if len(face_ranked) > 1
        else None
    )
    face_margin = (
        best_combined - second_best_similarity
        if second_best_similarity is not None
        else float("inf")
    )

    log("MATCH", f"Best combined similarity : {best_combined:.2f}%")
    log("MATCH", f"Best uploaded similarity : {best_uploaded_similarity:.2f}%")
    log("MATCH", f"Best live similarity     : {best_live_similarity:.2f}%")
    log(
        "MATCH",
        f"Best pHash distance      : {best_phash_distance} "
        f"(near_duplicate={best_is_near_duplicate})",
    )

    if second_best_similarity is not None:
        log(
            "MATCH",
            f"Second-best face score   : {second_best_similarity:.2f}%",
        )
        log(
            "MATCH",
            f"Biometric margin         : {face_margin:.2f} "
            f"(required {SIMILARITY_MARGIN:.1f})",
        )

    high_confidence_face = (
        best_uploaded_similarity >= IDENTITY_SIMILARITY_THRESHOLD
        and best_live_similarity >= IDENTITY_SIMILARITY_THRESHOLD
        and best_combined >= IDENTITY_SIMILARITY_THRESHOLD
    )

    near_duplicate_evidence = (
        best_is_near_duplicate
        and best_uploaded_similarity >= NEAR_DUPLICATE_SIMILARITY_THRESHOLD
        and best_live_similarity >= NEAR_DUPLICATE_SIMILARITY_THRESHOLD
    )

    if not (high_confidence_face or near_duplicate_evidence):
        exit_with_error(
            "No social-media result met the acceptance criteria. "
            f"Best uploaded={best_uploaded_similarity:.2f}%, "
            f"live={best_live_similarity:.2f}%, "
            f"combined={best_combined:.2f}%, "
            f"pHash distance={best_phash_distance}, "
            f"near_duplicate={best_is_near_duplicate}."
        )

    # Ambiguity guard applies to ordinary face-based acceptance. A near-
    # duplicate has a separate exact-photo evidence path.
    if (
        high_confidence_face
        and not near_duplicate_evidence
        and second_best_similarity is not None
        and face_margin < SIMILARITY_MARGIN
    ):
        exit_with_error(
            "Biometric match is ambiguous. "
            f"Best={best_combined:.2f}%, "
            f"second-best={second_best_similarity:.2f}%, "
            f"margin={face_margin:.2f} < "
            f"required {SIMILARITY_MARGIN:.1f}."
        )

    matched_url = best_url
    matched_similarity = best_combined

    if near_duplicate_evidence:
        log_success(
            "NEAR-DUPLICATE SOCIAL EVIDENCE VERIFIED."
        )
        log_success(
            f"pHash distance={best_phash_distance}; "
            f"uploaded-face similarity={best_uploaded_similarity:.2f}%; "
            f"live-face similarity={best_live_similarity:.2f}%."
        )
    else:
        log_success(
            f"BEST BIOMETRIC MATCH — combined similarity="
            f"{matched_similarity:.2f}%."
        )

    print()
    print(f"    Verified social URL : {matched_url}")
    print(f"    Face similarity     : {matched_similarity:.2f}%")
    print(f"    pHash distance      : {best_phash_distance}")

    # Step 3 — blockchain
    timestamp = int(time.time())
    face_hash = hash_encoding(live_encoding)

    post_hash = hashlib.sha256(matched_url.encode("utf-8") + best_image_bytes).hexdigest()

    log("POST", f"Post fingerprint (SHA-256): {post_hash}")
    log("FACE", f"On-chain face hash: {face_hash}")
    print(f"    Uploaded-photo hash (audit only): {uploaded_hash}")
    print()

    abi, bytecode = compile_contract()
    w3 = connect_web3(config["POLYGON_RPC_URL"])
    contract, account = get_contract(w3, config, abi, bytecode)

    tx_hash = submit_record(
        w3, contract, account, face_hash, matched_url, post_hash, timestamp, challenge_nonce
    )

    print()
    log("VERIFY", "Reading the latest verification record from Polygon…")

    verified = verify_record_on_chain(
        contract, face_hash, matched_url, post_hash, timestamp, challenge_nonce
    )

    if not verified:
        exit_with_error(
            "On-chain verification failed: the stored record did not match the submitted fingerprints."
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
