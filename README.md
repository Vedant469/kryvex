# Kryvex — Face Verification, Reverse-Image Search & Blockchain Evidence

Kryvex is a consent-based prototype pipeline that verifies a live person against a submitted face photo, searches the public web through Google Lens/SerpApi for a genuine image match, fingerprints the resulting evidence, and records the verification data on Polygon Amoy.

## Pipeline

```text
Submitted photo
      ↓
Exactly-one-face validation
      ↓
Two-blink webcam liveness
      ↓
AWS Rekognition: live ↔ submitted photo
      ↓
Google Lens / SerpApi
  ├─ submitted photo: exact + visual matches
  └─ live frame: exact + visual matches
      ↓
Social candidate extraction
      ↓
AWS Rekognition:
  submitted ↔ candidate
  live ↔ candidate
      ↓
pHash + biometric decision
      ↓
SHA-256 evidence fingerprint
      ↓
Polygon Amoy
      ↓
Read record back and verify
```

## Technologies

- Python
- OpenCV
- `face_recognition` / dlib for local face detection and blink/EAR liveness
- AWS Rekognition `CompareFaces` for biometric matching
- SerpApi Google Lens for genuine reverse-image discovery
- `imagehash` pHash for near-duplicate evidence
- SHA-256 for evidence fingerprinting
- Solidity + Web3.py
- Polygon Amoy testnet

## Requirements

Windows 10/11, Python environment, working webcam, internet access, AWS credentials with Rekognition access, SerpApi API key, and a Polygon Amoy wallet funded with testnet POL.

## Installation

```powershell
python -m pip install -r requirements.txt
python -m pip install boto3 imagehash
python -c "import boto3, imagehash; print('AWS + imagehash OK')"
python -m py_compile .\p2.py
```

## Configuration

Create `.env` from `.env.example`:

```text
SERPAPI_KEY=...
POLYGON_RPC_URL=...
WALLET_PRIVATE_KEY=...
CONTRACT_ADDRESS=
AWS_REGION=us-east-1
```

Never commit `.env`, AWS secrets, wallet private keys, or personal test photos.

Configure AWS locally:

```powershell
aws configure
aws sts get-caller-identity
```

## Running Kryvex

Normal run:

```powershell
python p2.py .\t4.jpg --camera 1
```

Save raw Lens responses:

```powershell
python p2.py .\t4.jpg --camera 1 --dump-results
```

Use `--skip-liveness` only for debugging:

```powershell
python p2.py .\t4.jpg --skip-liveness
```

## Decision logic

Current prototype settings:

```text
Live identity threshold                  90.0%
Identity candidate threshold             90.0%
Near-duplicate supporting threshold      60.0%
pHash maximum distance                   10
Biometric ambiguity margin                3.0
Liveness timeout                         30 seconds
```

The candidate score is:

```text
combined = 0.70 × uploaded_similarity
         + 0.30 × live_similarity
```

Multiple image variants belonging to one social URL are treated as one candidate. Broad directory/search pages are ignored when a specific profile/post URL is available.

## Blockchain evidence

The Solidity contract stores:

- face hash
- matched social URL
- post/evidence fingerprint
- timestamp
- challenge nonce

The final readback verifies all of these fields.

Network:

```text
Polygon Amoy
Chain ID: 80002
Solidity: 0.8.20
```

## Successful demonstration

The final successful test used the developer's own consented photo:

```text
Liveness                         PASS
Live ↔ submitted                 99.95%
Submitted ↔ LinkedIn candidate  100.00%
Live ↔ LinkedIn candidate       99.93%
Combined score                  99.98%
```

The specific LinkedIn result found was:

```text
https://in.linkedin.com/in/vedant-duduskar87
```

The run reached:

```text
✓ POST FINGERPRINT VERIFIED.
✓ ON-CHAIN VERIFICATION PASSED.
END-TO-END VERIFICATION COMPLETE
```

Recorded Polygon Amoy transaction:

```text
7e475fe6726f325d365f3b477fb21f3b1a185ddae2b2b7f2c866d456a664bed2
```

Recorded contract deployment:

```text
0x70996995053865FDe4b87c48F7e79c24ce227e18
```

These values are evidence from one test run; future runs may use different contract/transaction hashes.

## Limitations and responsible use

Kryvex is a prototype for consent-based verification. Google Lens is a reverse-image discovery service, not a dedicated biometric identity database. Visual matches may contain unrelated people, so Kryvex does not accept a top-ranked Lens result by itself.

The current liveness mechanism is a two-blink EAR heuristic and is not a production-grade presentation-attack-detection system.

The blockchain record provides an integrity/audit trail for the submitted verification evidence. It does not independently prove legal identity or account ownership.

If no genuine public match is returned, Kryvex should fail closed instead of inventing an identity match.

## Testing

See `TESTING.md` for the final manual test matrix, observed outputs, security checks, and the successful Polygon run.
