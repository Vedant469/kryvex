# Face ID + Blockchain Verification

A standalone Python pipeline that detects a face from an input image, proves a **live person** is at the webcam (blink challenge), performs a **genuine reverse-image search** via [SerpApi Google Lens](https://serpapi.com/google-lens-api), compares the live face to both the upload and the Lens match photo, and records the result on the **Polygon Amoy testnet**.

```
Input photo (exactly one face)
        ↓
Webcam liveness (blink twice + session nonce)
        ↓
Compare: live face vs uploaded photo
        ↓
SerpApi Google Lens reverse search
        ↓
Compare: live face vs Lens match image
        ↓
Polygon Amoy TX (live face hash + URL + timestamp + nonce)
```

## Features

| Step | Technology | Output |
|------|-----------|--------|
| Face detection & encoding | `face_recognition` (dlib HOG + 128-d vector) | SHA-256 of the **live** encoding |
| Liveness | Webcam + eye-aspect-ratio blinks + sharpness check | Session nonce; rejects still photos |
| Face compares | `face_distance` vs upload and vs Lens thumbnail | Same-person check (threshold 0.55) |
| Reverse image search | SerpApi Google Lens API (live, no hardcoded results) | Matching social-media URL |
| On-chain record | `VerificationLog.sol` on Polygon Amoy via `web3.py` | Immutable tx + `RecordAdded` event |

## Project Structure

```
face-id-blockchain-verification/
├── pipeline.py           # End-to-end orchestration script
├── VerificationLog.sol   # Solidity smart contract
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variable template
└── README.md
```

## Prerequisites (Linux Mint)

### 1. System packages

`face_recognition` depends on **dlib**, which must be compiled from source. A webcam is required for liveness.

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

> **Tip:** On Mint 21+ / Ubuntu 22.04+, Python 3.10+ is pre-installed.

### 2. API keys & wallet

| Resource | Where to get it |
|----------|----------------|
| SerpApi key | [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key) |
| Polygon Amoy RPC | `https://rpc-amoy.polygon.technology/` (free public endpoint) |
| Testnet MATIC | [Polygon faucet](https://faucet.polygon.technology/) — select **Amoy** network |
| Wallet private key | Export from MetaMask or create a new test wallet |

> **Security:** Never commit your `.env` file or share your private key.

## Setup

```bash
cd face-id-blockchain-verification

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
nano .env   # fill in SERPAPI_KEY, POLYGON_RPC_URL, WALLET_PRIVATE_KEY
```

### `.env` reference

```dotenv
SERPAPI_KEY=your_serpapi_key_here
POLYGON_RPC_URL=https://rpc-amoy.polygon.technology/
WALLET_PRIVATE_KEY=your_wallet_private_key_here
CONTRACT_ADDRESS=          # optional — leave blank to auto-deploy
```

If you previously deployed an older contract (3-argument `addRecord`), **leave `CONTRACT_ADDRESS` empty** so the new 4-argument contract is deployed.

## Usage

```bash
source .venv/bin/activate

# Full pipeline: photo + webcam blinks + Lens + chain
python pipeline.py /path/to/photo.jpg

# Other camera, debug Lens JSON, or skip liveness (debug only)
python pipeline.py /path/to/photo.jpg --camera 1
python pipeline.py /path/to/photo.jpg --dump-results
python pipeline.py /path/to/photo.jpg --skip-liveness   # NOT secure
```

A window titled **Liveness challenge** opens. Look at the camera and **blink twice**. Press `Q` to abort.

## Polygon Amoy Testnet

| Property | Value |
|----------|-------|
| Network name | Polygon Amoy Testnet |
| Chain ID | `80002` |
| RPC URL | `https://rpc-amoy.polygon.technology/` |
| Currency | MATIC (testnet) |
| Block explorer | [amoy.polygonscan.com](https://amoy.polygonscan.com/) |
| Faucet | [faucet.polygon.technology](https://faucet.polygon.technology/) |

Stored on-chain:

- `faceHash` — SHA-256 of the **live** 128-d encoding
- `matchedUrl` — social-media URL from Google Lens
- `timestamp` — Unix epoch seconds
- `challengeNonce` — one-time blink-session code (`getRecord`)

## Smart Contract

```solidity
function addRecord(
    string calldata faceHash,
    string calldata matchedUrl,
    uint256 timestamp,
    string calldata challengeNonce
) external;
event RecordAdded(string faceHash, string matchedUrl, uint256 timestamp);
```

## Known Limitations

### Liveness

- Blink detection stops **printed stills**. A **replayed video** of someone blinking on a phone can still pass.
- `--skip-liveness` disables this protection.

### Reverse-image search

- Public/indexed images only. Lookalikes and twins can still be close in `face_distance`.
- Finding a URL does **not** prove the person **owns** that social account.

### Blockchain

- Amoy is a testnet. Anyone with the ABI and gas can still call `addRecord` (no owner lock yet).

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No face detected` | One clear, front-facing photo. |
| `Found N faces` | Crop so only one person is in the frame. |
| `Cannot open webcam` | Close other camera apps; try `--camera 1`. |
| `Liveness failed` | Face the camera, blink clearly; do not hold up a photo. |
| `Face mismatch` | Person at the camera is not the person in the file / Lens result. |
| `No social-media match found` | Use a photo that is actually on a public social profile. |
| `Wallet has 0 MATIC` | Fund via the [Amoy faucet](https://faucet.polygon.technology/). |
| Old contract / revert on `addRecord` | Clear `CONTRACT_ADDRESS` and redeploy. |

## License

MIT
