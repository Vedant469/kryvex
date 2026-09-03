# Testing

## Testing Strategy

Kryvex has been validated through six manually-executed developer tests covering the core workflow and edge cases. These are **not automated CI tests** — they are representative end-to-end scenarios run by developers in a local environment.

Testing focuses on:

1. **Valid identity path** — correct person, liveness passed, social match found, blockchain recorded
2. **Wrong-person rejection** — live face distance exceeds threshold, pipeline stops immediately
3. **Liveness failure** — incomplete blink challenge, pipeline times out
4. **Multiple-face rejection** — input image contains > 1 face, rejected before liveness
5. **Invalid image handling** — non-image file, clean error message
6. **Cryptographic behavior** — hash determinism, sensitivity to input changes

## Test Environment

- **Platform:** Windows 10/11
- **Shell:** PowerShell
- **Webcam:** Local webcam
- **Network:** Public internet (SerpApi, Polygon RPC)
- **Blockchain:** Polygon Amoy testnet (chain ID 80002)
- **Smart contract:** `VerificationLog.sol` (0.8.20 Solidity)

## Test Summary

| Test Scenario Expected Actual Result  |                                                                            |                                                           |                                       |          |
| ------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------- | ------------------------------------- | -------- |
| A                                     | Valid end-to-end: correct person, liveness, social match, blockchain write | All checks pass; on-chain verified                        | All checks passed; [VERIFY] confirmed | **PASS** |
| B                                     | Wrong person at webcam, face distance > threshold                          | Rejected immediately after face mismatch                  | Rejected with distance 0.8081 > 0.55  | **PASS** |
| C                                     | Liveness timeout: only 1 blink detected                                    | Pipeline times out; no social/blockchain steps            | Timeout at 30s after 1 blink          | **PASS** |
| D                                     | Multiple faces in input image                                              | Rejected at face detection; no liveness                   | Rejected; 3 faces detected            | **PASS** |
| E                                     | Non-image file (text) as input                                             | Clean error; no traceback                                 | `Could not decode input image`        | **PASS** |
| F                                     | Hash sensitivity: same URL+image vs. modified inputs                       | Same match = identical hash; changes cause different hash | All three scenarios verified          | **PASS** |

## Test A — Valid End-to-End Pipeline

**Input:** Single-person face photo
**Objective:** Verify full workflow including blockchain write/readback

### Face Detection & Encoding

```
[FACE] Detecting faces in image…
✓ Face encoded — SHA-256 hash: ee490bc1b31ef02418bfee8373afbf15c3c4d3b0e33a9f179f154f78f1abfdc4

```

**Face hash:** `ee490bc1b31ef02418bfee8373afbf15c3c4d3b0e33a9f179f154f78f1abfdc4`

### Liveness Challenge

```
[LIVENESS] Blink detected (1/2).
[LIVENESS] Blink detected (2/2).
✓ Liveness challenge passed.

```

- **Blink 1:** Detected ✓
- **Blink 2:** Detected ✓
- **Duration:** Within 30-second timeout
- **Sharpness:** Met minimum threshold

### Face Distance (Live vs. Uploaded)

```
[MATCH] live webcam vs uploaded photo: distance=0.5327 (threshold 0.55)
✓ Live webcam matches uploaded face.

```

- **Distance:** 0.5327
- **Threshold:** 0.55
- **Result:** Match (distance ≤ threshold)

### Reverse Image Search

```
[SERPAPI] Uploading image to SerpApi Image API…
✓ Image uploaded
[SERPAPI] Running Google Lens reverse image search…
✓ Google Lens search completed.
[SERPAPI] Scanning results for social-media matches…
✓ 14 social-media candidates found.

```

- **Google Lens results:** 14 social-media candidates

### Candidate Matching

**Best candidate selected:**

- **URL:** `https://www.reddit.com/r/glassesadvice/comments/1t2m14e/does_this_frame_suit_me/`
- **Face distance:** 0.4851
- **Source:** Reddit (social media)

### Post Fingerprint

```
post_hash = SHA-256(matched_url.encode("utf-8") + best_image_bytes)
         = fdadd772e9501ff925e6b8a943e5d5c118aaeb866936f03ffbcdb24739ed124c

```

**Post fingerprint (evidence):** `fdadd772e9501ff925e6b8a943e5d5c118aaeb866936f03ffbcdb24739ed124c`

### Blockchain Submission

```
[BLOCKCHAIN] Deploying VerificationLog contract to Amoy…
✓ Contract deployed at: 0x89a19843238ce21bb3f061bE5a1cBe12a9762Cee
 PolygonScan: https://amoy.polygonscan.com/address/0x89a19843238ce21bb3f061bE5a1cBe12a9762Cee

```

**Contract deployed:** `0x89a19843238ce21bb3f061bE5a1cBe12a9762Cee`

```
[BLOCKCHAIN] Writing verification record to Polygon Amoy…
[BLOCKCHAIN] Transaction sent: 0x7b3c3f840aa8bf8ffe70d874acbab60831e59d66ada48f7e8044922225db9683
✓ Verification record confirmed in block 46557963.
 Transaction: https://amoy.polygonscan.com/tx/0x7b3c3f840aa8bf8ffe70d874acbab60831e59d66ada48f7e8044922225db9683

```

- **Deployment TX:** `5cb705fd5e849f4ad52c8e7e89fdbab842e52af141f1a1be493b502940a98096`
- **Verification TX:** `7b3c3f840aa8bf8ffe70d874acbab60831e59d66ada48f7e8044922225db9683`
- **Block:** 46557963

### On-Chain Verification

```
[VERIFY] Face hash: ✓
[VERIFY] Social URL: ✓
[VERIFY] Post fingerprint: ✓
[VERIFY] Timestamp: ✓
✓ POST FINGERPRINT VERIFIED.
✓ ON-CHAIN VERIFICATION PASSED.
END-TO-END VERIFICATION COMPLETE

```

- **Face hash match:** ✓
- **URL match:** ✓
- **Post fingerprint match:** ✓
- **Timestamp match:** ✓
- **Final result:** PASS

---

## Test B — Wrong Person (Face Mismatch)

**Input:** Photo of person A; person B at webcam
**Objective:** Verify rejection when live face does not match uploaded face

### Face Detection & Encoding

Uploaded photo detected and encoded successfully.

### Liveness Challenge

```
[LIVENESS] Blink detected (1/2).
[LIVENESS] Blink detected (2/2).
✓ Liveness challenge passed.

```

Liveness passed (person B blinked twice).

### Face Distance Check

```
[MATCH] live webcam vs uploaded photo: distance=0.8081 (threshold 0.55)
✗ Live person does not match uploaded photo. Distance 0.8081 > 0.55.

```

- **Distance:** 0.8081
- **Threshold:** 0.55
- **Comparison:** 0.8081 > 0.55 (REJECT)

### Pipeline Termination

```
Pipeline stopped immediately. No reverse image search. No blockchain steps.

```

**Result:** PASS (correct rejection)

---

## Test C — Liveness Failure (Timeout)

**Input:** Valid face photo; only 1 blink detected
**Objective:** Verify timeout when liveness challenge not completed

### Face Detection & Encoding

Uploaded photo detected and encoded successfully.

### Liveness Challenge

```
[LIVENESS] Blink detected (1/2).
✗ Liveness challenge timed out. Make sure your face is visible and blink twice clearly.

```

- **Blink 1:** Detected
- **Blink 2:** Not detected within 30 seconds
- **Result:** Timeout

### Pipeline Termination

```
Pipeline stopped at liveness stage. No face matching. No social search. No blockchain.

```

**Result:** PASS (correct timeout and rejection)

---

## Test D — Multiple Faces

**Input:** Image containing 3 faces (multi-person photo)
**Objective:** Verify rejection before liveness stage

### Face Detection

```
[FACE] Detecting faces in image…
✗ Found 3 faces in the image. Use a photo with exactly one visible face.

```

- **Faces detected:** 3
- **Expected:** 1
- **Result:** Rejected

### Pipeline Termination

```
Pipeline stopped at face detection. No liveness. No matching. No blockchain.

```

**Result:** PASS (correct early rejection)

---

## Test E — Invalid Image File

**Input:** Text file named `not_image.txt`
**Objective:** Verify graceful error handling for non-image input

### Image Loading

```
✗ Could not decode input image: cannot identify image file 'not_image.txt'

```

- **Error message:** Clean, descriptive
- **Traceback:** None (handled gracefully)

**Result:** PASS (clean error handling)

---

## Test F — Hash Tamper Sensitivity

**Objective:** Verify SHA-256 fingerprint determinism and sensitivity to input changes

### Test Setup

A temporary test script (`test_hash.py`) computed SHA-256 fingerprints under three scenarios:

**Scenario 1: Same URL + Same Image Bytes**

```
hash1 = SHA-256(url1 + bytes1)
hash2 = SHA-256(url1 + bytes1)
Result: hash1 == hash2  →  True

```

**Scenario 2: Same URL + Modified Image Bytes**

```
hash1 = SHA-256(url1 + bytes1)
hash2 = SHA-256(url1 + bytes2_modified)  # Modified image bytes
Result: hash1 == hash2  →  False

```

**Scenario 3: Modified URL + Same Image Bytes**

```
hash1 = SHA-256(url1 + bytes1)
hash2 = SHA-256(url2_modified + bytes1)
Result: hash1 == hash2  →  False

```

### Results

```
Same matches        : True
Image changed       : True
URL changed         : True

```

**Interpretation:**

- Hash is deterministic (same input → same output)
- Hash is sensitive to any change in URL
- Hash is sensitive to any change in image bytes

**Result:** PASS (local hash determinism and sensitivity verified)

### Important Caveat

Test F validated hash determinism **locally**, not an on-chain tampering attack. It does not:

- Attempt to modify a blockchain record (immutable by design)
- Demonstrate vulnerability to hash collisions (SHA-256 collision search is computationally infeasible)
- Prove anything about the social-media image remaining unchanged

---

## Limitations & Unverified Cases

### Automated Testing

The current project has **no automated CI/CD test suite**. The six tests (A–F) above are:

- **Manually executed** by developers
- **Reproducible but not automated** (no pytest, no GitHub Actions)
- **Representative**, not exhaustive

Automated testing would require:

- Headless webcam simulation (e.g., OpenCV video file playback)
- Mocked SerpApi responses
- Local Polygon Amoy testnet (Hardhat/Ganache)
- CI/CD pipeline integration

### Liveness Robustness

- One-blink detection (Test C) is a simple timeout test
- No systematic evaluation of video replay resistance
- No testing with masks, glasses, makeup, or lighting variations
- Blink threshold (EAR < 0.21) is not calibrated across demographics

### Face Distance Calibration

- Threshold 0.55 is **an implementation parameter, not scientifically validated**
- No sensitivity analysis (e.g., effect of age, gender, ethnicity on distance)
- Test B uses distance 0.8081 vs. 0.4851 (clear separation, not boundary cases)
- Boundary behavior (distances near 0.55) is untested

### Social Media Matching

- Test A found 14 candidates; no test with 0 candidates
- No validation of non-social results (e.g., news, e-commerce)
- No test of duplicate URLs across multiple platforms
- No assessment of false-positive rate across different images

### Blockchain Verification

- `challengeNonce` is stored but **NOT compared** in `verify_record_on_chain()` 
  - This is a current limitation, not a missing feature
  - See code: function checks face hash, URL, post hash, timestamp only
- No test of contract re-deployment or state migration
- No adversarial test (e.g., calling `addRecord` with fabricated data)
- No test of concurrent transactions or gas estimation edge cases

### Account Ownership

- Test A demonstrates that a face can be matched to a social URL
- **It does NOT prove the account belongs to the person**
- No test verifies username, followers, or account metadata
- No test of compromised or impersonated accounts

### Post Fingerprint Scope

- Hash covers matched URL + image bytes only
- No test validates immutability of the social-media platform's content
- Images can be deleted, reposts can appear, metadata can change
- Fingerprint is **evidence** of what was found, not proof of truth

### Deployment & Scaling

- All tests run on Polygon Amoy testnet (temporary, no value)
- No testing on Polygon Mumbai or mainnet
- Gas estimation is hardcoded (1M for deployment); no stress test
- No testing with large batches of records or high transaction throughput

---

## How to Run Tests Locally

### Test A (End-to-End)

```bash
python pipeline.py ./valid_face_photo.jpg
# Look at camera, blink twice
# Observe blockchain confirmation

```

### Test B (Wrong Person)

```bash
python pipeline.py ./person_a_photo.jpg
# Have person B sit at camera and blink
# Expect rejection at face distance check

```

### Test C (Liveness Timeout)

```bash
python pipeline.py ./valid_face_photo.jpg
# Blink only once
# Wait for 30-second timeout

```

### Test D (Multiple Faces)

```bash
python pipeline.py ./group_photo_3_people.jpg
# Expect immediate rejection

```

### Test E (Invalid Image)

```bash
python pipeline.py ./not_an_image.txt
# Expect clean error message

```

### Test F (Hash Sensitivity)

```bash
# Use the temporary test script provided during development
python test_hash.py
# Verify determinism and sensitivity

```

---

## Conclusion

**Overall Status:** 6/6 manual tests PASS

Kryvex successfully demonstrates:

- ✓ End-to-end biometric liveness + reverse image search + blockchain integration
- ✓ Correct rejection of wrong-person cases
- ✓ Timeout handling for incomplete liveness challenges
- ✓ Early rejection of invalid or multi-face inputs
- ✓ Cryptographically sound fingerprinting
- ✓ Immutable blockchain recording and readback

**Caveats:**

- Manual testing only (no CI/CD automation)
- Liveness and face-distance thresholds are research-grade, not production-validated
- Social account ownership is not cryptographically established
- Testnet only (no mainnet deployment)
- Current limitation: `challengeNonce` stored but not verified on-chain

For production deployment, consider the recommendations in README.md § Future Improvements.