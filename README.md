# Kryvex: Face ID & Blockchain Verification

A standalone Python pipeline that verifies a live person against an uploaded face photo, performs genuine reverse-image search via SerpApi Google Lens, and records cryptographic evidence on the Polygon Amoy testnet.

## Overview

Kryvex addresses a critical challenge in identity verification: confirming that a specific person is physically present at a webcam, identifying them across public web/social-media records, and creating an immutable record of that evidence.

The pipeline combines:

- **Biometric liveness detection** — a two-blink webcam challenge to reject static photos and video replays
- **Face encoding matching** — comparing live and uploaded faces using dlib's 128-dimensional face embeddings
- **Reverse image search** — SerpApi Google Lens API to find where an image appears publicly
- **Social media discovery** — filtering results to social platforms (Reddit, Instagram, TikTok, etc.)
- **Cryptographic fingerprinting** — SHA-256 hash of the matched URL and downloaded candidate image
- **Blockchain immutability** — storing the fingerprint on Polygon Amoy testnet via a Solidity smart contract

**What Kryvex is NOT:**

- Not a proof of account ownership (biometric match alone does not prove social account ownership)
- Not a production identity service (research-grade liveness and face-distance thresholds)
- Not a foolproof spoofing defense (printed replays with synchronized blinks may bypass liveness)
- Not a complete post archive (the fingerprint covers URL + image bytes, not post text/comments/metadata)

## Features

| Component Technology Purpose  |                                              |                                            |
| ----------------------------- | -------------------------------------------- | ------------------------------------------ |
| Face detection & encoding     | `face_recognition` (dlib HOG + 128-d)        | Extract face embeddings from images        |
| Liveness challenge            | Webcam + eye-aspect-ratio blinks + sharpness | Reject still photos and video replays      |
| Face distance comparison      | `face_recognition.face_distance()`           | Biometric matching (threshold: 0.55)       |
| Reverse image search          | SerpApi Google Lens API                      | Find images on public web/social platforms |
| Social media filtering        | URL domain scanning                          | Identify social-platform results           |
| Image download & decode       | `requests` + `Pillow`                        | Retrieve and validate candidate images     |
| Cryptographic fingerprint     | SHA-256(`matched_url + image_bytes`)         | Evidence immutability marker               |
| Smart contract                | Solidity 0.8.20 (`VerificationLog.sol`)      | Immutable record storage                   |
| Blockchain submission         | `web3.py` + Polygon Amoy RPC                 | Write and read verification records        |

## Architecture

```
┌─────────────────────────────────┐
│   User Input (face image)       │
└──────────────┬──────────────────┘
               │
               ▼
       ┌───────────────────┐
       │  Face Detection   │
       │  & Encoding       │
       │  SHA-256 hash     │
       └─────────┬─────────┘
                 │
                 ▼
       ┌───────────────────────┐
       │  Liveness Challenge   │
       │  (Blink Detection)    │
       │  2 blinks in 30s      │
       └─────────┬─────────────┘
                 │
                 ▼
       ┌───────────────────────┐
       │  Face Distance Check  │
       │  Live vs. Uploaded    │
       │  Threshold: 0.55      │
       └─────────┬─────────────┘
                 │
                 ▼
       ┌───────────────────────┐
       │  SerpApi Image Upload │
       │  Google Lens Search   │
       └─────────┬─────────────┘
                 │
                 ▼
       ┌───────────────────────────┐
       │  Social Media Matching    │
       │  Find candidate images    │
       │  Download & face detect   │
       └─────────┬─────────────────┘
                 │
                 ▼
       ┌───────────────────────────┐
       │  Best Candidate Selection │
       │  Lowest face distance     │
       │  Create fingerprint       │
       └─────────┬─────────────────┘
                 │
                 ▼
       ┌───────────────────────────┐
       │  Polygon Amoy TX          │
       │  Write VerificationLog    │
       │  Read back & verify       │
       └───────────────────────────┘

```

## Project Structure

```
kryvex/
├── pipeline.py              # Main orchestration script (1122 lines)
├── VerificationLog.sol      # Solidity smart contract (72 lines)
├── requirements.txt         # Python dependencies
├── pyproject.toml           # Project metadata (uv)
├── uv.lock                  # Lock file (uv)
├── .env.example             # Environment variable template
├── .env                     # Local secrets (DO NOT COMMIT)
├── .gitignore               # Git ignore rules
├── .python-version          # Python version constraint
├── README.md                # This file
├── TESTING.md               # Test results & methodology
└── src/kryvex/              # Namespace directory (optional)

```

## Prerequisites

### System Requirements

- **Python 3.10+** (tested on Linux Mint 21+, Ubuntu 22.04+)
- **Webcam** (required for liveness challenge)
- **\~2 GB free disk** (for dlib compilation and dependencies)

### System Packages (Linux)

`face_recognition` requires dlib to be compiled from source. Install build dependencies:

```bash
sudo apt update
sudo apt install -y \
  python3 python3-pip python3-venv \
  build-essential cmake \
  libopenblas-dev liblapack-dev \
  libx11-dev libgtk-3-dev \
  libboost-python-dev \
  v4l-utils

```

**macOS:** Xcode Command Line Tools + Homebrew packages

```bash
xcode-select --install
brew install cmake openblas lapack boost

```

**Windows:** Visual C++ Build Tools + CMake (challenging for dlib; consider WSL2 or Docker)

### API Keys & Blockchain

| Resource Source        |                                                                        |
| ---------------------- | ---------------------------------------------------------------------- |
| **SerpApi key**        | [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key)       |
| **Polygon Amoy RPC**   | `https://rpc-amoy.polygon.technology/` (free public endpoint)          |
| **Testnet MATIC**      | [faucet.polygon.technology](https://faucet.polygon.technology/) → Amoy |
| **Wallet private key** | Export from MetaMask or create new test wallet (64 hex chars)          |

## Setup

### 1. Clone and prepare environment

```bash
git clone https://github.com/Vedant469/kryvex.git
cd kryvex

python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt

```

### 2. Configure secrets

```bash
cp .env.example .env
nano .env   # or your editor

```

Fill in the four required variables:

```
SERPAPI_KEY=your_serpapi_api_key_here
POLYGON_RPC_URL=https://rpc-amoy.polygon.technology/
WALLET_PRIVATE_KEY=your_wallet_private_key_hex_here
CONTRACT_ADDRESS=          # Leave empty for auto-deployment

```

**Security note:** Never commit `.env` or share your private key. Use `.gitignore` to exclude it.

### 3. (Optional) Specify contract address

If you've previously deployed `VerificationLog` and wish to reuse it:

```
CONTRACT_ADDRESS=0xYourContractAddressHere

```

Leave it blank to auto-deploy a new instance on each first run.

## Usage

### Basic workflow

```bash
# Activate venv first
source .venv/bin/activate

# Run the full pipeline with a face photo
python pipeline.py /path/to/your/face.jpg

```

The script will:

1. Load and validate the image
2. Detect and encode the face
3. Open a webcam window titled **"Kryvex Liveness"** 
   - **Blink twice** clearly within 30 seconds
   - Press **Q** to cancel
4. Compare live face to uploaded face
5. Upload to SerpApi and run Google Lens reverse search
6. Test social-media candidate images
7. Find the best match (lowest face distance)
8. Submit record to Polygon Amoy
9. Verify the blockchain record

### Command-line options

```bash
# Different camera device (default is 0)
python pipeline.py photo.jpg --camera 1

# Save raw SerpApi JSON for debugging
python pipeline.py photo.jpg --dump-results

# Skip liveness (DEBUG ONLY — NOT SECURE)
python pipeline.py photo.jpg --skip-liveness

```

**Warning:** `--skip-liveness` disables the blink challenge and should never be used in production. It allows anyone at the webcam to bypass biometric verification.

## Technical Details

### Liveness Challenge

The webcam-based liveness check requires:

1. **Exactly one face** visible in the frame
2. **Two distinct blinks** detected via eye-aspect-ratio (EAR) analysis
3. **Sharpness validation** (Laplacian variance ≥ 35.0) to reject blurry frames
4. **Completion within 30 seconds**

**Limitations:**

- Stops **printed stills** (no moving eyes)
- Does NOT reliably stop **video replays** (phone/screen with synchronized blinks)
- EAR threshold (0.21) is heuristic-based, not scientifically validated

### Face Matching

- Uses dlib's ResNet-based 128-dimensional face encodings
- Calculates Euclidean distance between encodings
- **Threshold: 0.55** (current implementation parameter) 
  - Distance ≤ 0.55: considered a match
  - Distance > 0.55: rejected as non-matching
- **Not scientifically calibrated** — threshold may require tuning for different populations, lighting conditions, and use cases

### Reverse Image Search

- **SerpApi Google Lens API** performs the search
- Searches across indexed public web images
- Returns results from social media platforms, image sites, and news
- Filtering logic isolates social-media domains: Instagram, Facebook, TikTok, X/Twitter, Reddit, LinkedIn, Threads, YouTube, Pinterest, Snapchat

### Candidate Selection

For each social-media result:

1. Extract image candidates from the result object
2. Download each candidate image (max 8 MB)
3. Validate Content-Type (reject HTML/login pages)
4. Detect faces using dlib HOG + CNN fallback
5. Accept only images with **exactly one face**
6. Calculate face distance vs. live face encoding
7. Select the candidate with the **lowest distance** (best match)

### Post Fingerprint

The "evidence fingerprint" is computed as:

```
post_hash = SHA-256( matched_url.encode("utf-8") + best_image_bytes )

```

**What it covers:**

- The matched social-media URL
- The exact bytes of the downloaded candidate image

**What it does NOT cover:**

- Post caption/text
- Author metadata
- Comments, likes, timestamps from the platform
- The complete HTML page
- Any other context

**Why this matters:** The fingerprint records the exact matched URL and downloaded image bytes observed during verification. It does not independently prove the authenticity, ownership, or truthfulness of the social-media content.

### Polygon Amoy Blockchain

**Network Details:**

| Property Value  |                                                                 |
| --------------- | --------------------------------------------------------------- |
| Network         | Polygon Amoy Testnet                                            |
| Chain ID        | 80002                                                           |
| RPC URL         | `https://rpc-amoy.polygon.technology/`                          |
| Currency        | MATIC (testnet, no real value)                                  |
| Block explorer  | [amoy.polygonscan.com](https://amoy.polygonscan.com/)           |
| Faucet          | [faucet.polygon.technology](https://faucet.polygon.technology/) |

**Smart Contract:**

The `VerificationLog` contract stores verification records immutably.

**Function:**

```solidity
function addRecord(
    string calldata faceHash,
    string calldata matchedUrl,
    string calldata postHash,
    uint256 timestamp,
    string calldata challengeNonce
) external

```

**Record Struct:**

```solidity
struct Record {
    string faceHash;        // SHA-256 of live face encoding
    string matchedUrl;      // Social-media URL from Google Lens
    string postHash;        // SHA-256(URL + image_bytes)
    uint256 timestamp;      // Unix epoch seconds
    string challengeNonce;  // Session nonce from liveness challenge
}

```

**Reading Back:**

```solidity
function getRecord(uint256 index) external view returns (...)
function recordCount() external view returns (uint256)

```

**Current limitations:**

- No owner/access control — anyone can call `addRecord` (test contract behavior)
- `challengeNonce` is stored but **NOT compared** during on-chain verification (see TESTING.md)
- Polygon Amoy is a testnet — data is temporary and for development only

### On-Chain Verification

After writing a record, the pipeline reads it back and compares:

1. **Face hash** — must match the computed local hash
2. **Matched URL** — must match the submitted URL
3. **Post fingerprint** — must match the locally computed SHA-256
4. **Timestamp** — must match (within the same second)

All four checks must pass for the final confirmation message.

## Dependencies

See `requirements.txt`:

```
face-recognition==1.3.0      # Face detection & encoding (dlib-based)
opencv-python==4.10.0.84     # Image/video processing
numpy==1.26.4                # Numerical arrays
Pillow==10.4.0               # Image I/O
web3==7.4.0                  # Ethereum/Polygon blockchain interaction
google-search-results==2.4.2 # SerpApi wrapper
python-dotenv==1.0.1         # Environment variable loading
requests==2.32.3             # HTTP client
py-solc-x==2.0.3             # Solidity compiler

```

## Troubleshooting

| Issue Diagnosis & Fix             |                                                                  |
| --------------------------------- | ---------------------------------------------------------------- |
| `No face detected`                | Use a clear, front-facing photo with good lighting               |
| `Found 3 faces`                   | Crop so only one person is visible                               |
| `Cannot open webcam`              | Close other camera apps; check permissions; try `--camera 1`     |
| `Liveness failed / timeout`       | Face the camera, blink clearly; don't hold up a printed photo    |
| `Face mismatch (distance > 0.55)` | The person at the webcam is not the person in the uploaded photo |
| `No social-media match found`     | Use a photo that is actually indexed on a public social profile  |
| `Wallet has 0 MATIC`              | Fund via [Amoy faucet](https://faucet.polygon.technology/)       |
| `SerpApi upload failed`           | Image > 500 KB; compress it first                                |
| `Old contract / revert`           | Clear `CONTRACT_ADDRESS` from `.env` to redeploy                 |
| `Python dlib compilation fails`   | Install build tools (`apt install build-essential cmake`)        |

## Limitations

### Liveness

- Video replay attacks with synchronized blinks may bypass the check
- Static images without the required blink behavior are rejected by the current liveness challenge
- No protection against sophisticated deepfakes (out of scope)

### Face Matching

- Threshold (0.55) is not scientifically validated across populations
- Twins and lookalikes may produce false positives
- Lighting, camera angle, and facial expressions affect distance
- Single 128-d embedding per face is a simplification

### Reverse Image Search

- Only finds **indexed, public images**
- Non-indexed images on private profiles are invisible
- Google Lens quality and coverage vary by region

### Social Media Linking

- Finding a face on a social URL does NOT prove account ownership
- Accounts change ownership; profiles can be impersonated
- Screenshots or reposts create false matches

### Blockchain

- Amoy is a testnet with no real-world trust
- Contract has no owner/access control (intentional for testing)
- `challengeNonce` is stored but not currently verified in readback
- Immutability is technical only — social metadata is mutable

### Security

- Webcam can be compromised (malware, browser access)
- API keys in `.env` must be kept secret and rotated regularly
- Private key exposure compromises wallet security
- Network traffic should use HTTPS (not intercepted in this implementation)

## Future Improvements

1. **Multifactor liveness** — infrared, depth sensing, or 3D face geometry
2. **Threshold calibration** — population-specific, environment-adaptive tuning
3. **Owner/access control** — protect contract from spam via `Ownable` pattern
4. **Challenge replay protection** — verify `challengeNonce` on-chain
5. **Distributed reverse search** — backup providers (Yandex, Bing, TinEye)
6. **Post metadata hashing** — include captions, author, timestamp if feasible
7. **Optical character recognition (OCR)** — detect text overlays in candidate images
8. **Temporal verification** — cross-check upload dates and account creation
9. **Mainnet deployment** — move to Polygon Mumbai or Ethereum mainnet with production contracts
10. **Mobile app** — native iOS/Android client with local face processing

## Testing

See [**TESTING.md**](https://claude.ai/chat/TESTING.md) for:

- End-to-end test results (Test A: valid pipeline)
- Wrong-person rejection (Test B: distance > threshold)
- Liveness failure (Test C: timeout)
- Multiple-face rejection (Test D)
- Invalid image handling (Test E)
- Hash determinism (Test F)
- Current limitations and caveats

## License

MIT License. See repository for details.

## Context

Developed for the **HH Goa 2026 Shortlisting Task 3** (Hackathon Hacker House, Goa).

**Built by:** [Vedant Duduskar](https://github.com/Vedant469)

For questions, issues, or contributions, please open an issue or pull request on GitHub.