# Kryvex — Final Testing Report

## Test environment

```text
OS              Windows 11
Shell           PowerShell
Camera          Phone camera through DroidCam
Camera index    1
AWS             Rekognition CompareFaces
AWS region      us-east-1
Reverse search  Google Lens via SerpApi
pHash           imagehash.phash
Blockchain      Polygon Amoy
Chain ID        80002
Solidity        0.8.20
Pipeline file   p2.py
```

## Final test matrix

| ID | Test | Command / Input | Expected | Observed | Result |
|---|---|---|---|---|---|
| T01 | Valid end-to-end verification | `python p2.py .\t4.jpg --camera 1` | Liveness + biometric + real web match + blockchain verification | Liveness passed; live/submitted 99.95%; LinkedIn profile found; candidate 100%/99.93%; Polygon readback passed | PASS |
| T02 | Wrong-person rejection | `python p2.py .\test2.jpeg --camera 1` | Reject after live/submitted mismatch | Similarity 30.77% < 90%; rejected before social search | PASS |
| T03 | Multiple-face rejection | `python p2.py .\t2.jpeg --camera 1` | Reject before liveness | `Found 3 faces in the image` | PASS |
| T04 | Liveness failure | `python p2.py .\t4.jpg --camera 1` with incomplete blinks | Reject | Challenge timeout/cancellation | PASS |
| T05 | Invalid image | `python p2.py .\not_image.txt` | Clean decode error | Invalid image was rejected | PASS |
| T06 | AWS sanity check | `aws_face_test.py` using same image twice | Very high similarity | 100.00% similarity, 100.00% face confidence | PASS |
| T07 | Reverse-image no-result behavior | Lens query with no exact indexed result | Continue/fail closed | Empty exact-match result is non-fatal; no invented identity | PASS |
| T08 | Hash determinism | Same URL + same bytes | Same SHA-256 | Same evidence produced same hash | PASS |
| T09 | Image tamper detection | Change image bytes | Hash changes | Modified image produced different hash | PASS |
| T10 | URL tamper detection | Change URL | Hash changes | Modified URL produced different hash | PASS |
| T11 | Polygon readback | Successful T01 transaction | All stored fields match | Face hash, URL, post hash, timestamp, nonce all verified | PASS |

## T01 — Valid end-to-end verification

Command:

```powershell
python p2.py .\t4.jpg --camera 1
```

Observed:

```text
✓ Liveness challenge passed.
[MATCH] Live identity similarity: 99.95% (required 90.0%)
✓ Live webcam matches uploaded face
```

Google Lens returned the specific LinkedIn profile:

```text
https://in.linkedin.com/in/vedant-duduskar87
```

Candidate verification:

```text
uploaded photo vs social image: 100.00%
live webcam vs social image:     99.93%
combined similarity:             99.98%
```

The pipeline then generated the evidence fingerprint, connected to Polygon Amoy, deployed the verification contract, submitted a record, and read the record back.

Final checks:

```text
Face hash: ✓
Social URL: ✓
Post fingerprint: ✓
Timestamp: ✓
Challenge nonce: ✓
POST FINGERPRINT VERIFIED.
ON-CHAIN VERIFICATION PASSED.
END-TO-END VERIFICATION COMPLETE
```

Observed transaction:

```text
7e475fe6726f325d365f3b477fb21f3b1a185ddae2b2b7f2c866d456a664bed2
```

Observed contract:

```text
0x70996995053865FDe4b87c48F7e79c24ce227e18
```

## T02 — Wrong person

Command:

```powershell
python p2.py .\test2.jpeg --camera 1
```

Observed:

```text
✓ Liveness challenge passed.
[MATCH] Live identity similarity: 30.77% (required 90.0%)
✗ Live person does not match uploaded photo.
```

The pipeline stopped before social discovery and blockchain submission.

## T03 — Multiple faces

Command:

```powershell
python p2.py .\t2.jpeg --camera 1
```

Observed:

```text
✗ Found 3 faces in the image.
Use a photo with exactly one visible face.
```

The pipeline stopped during input validation.

## T04 — Liveness failure

Command:

```powershell
python p2.py .\t4.jpg --camera 1
```

Procedure: start the challenge but do not complete both blinks.

Expected/observed behavior:

```text
✗ Liveness challenge timed out.
```

or cancellation.

## T05 — Invalid image

Example:

```powershell
python p2.py .\not_image.txt
```

Expected behavior is a clean image-decoding error rather than proceeding to biometric verification.

## T06 — AWS sanity check

The direct AWS test compared the same image against itself.

Observed:

```text
Similarity=100.00%
FaceConfidence=100.00%
```

This confirms AWS credentials, SDK access, and the Rekognition comparison path.

It is a sanity check and should not be interpreted as the expected score for every real-world different photograph.

## T07 — Reverse-image fail-closed behavior

The pipeline calls Google Lens using the genuine SerpApi API.

Some queries can return no exact results. Kryvex treats an empty exact-match response as a retrieval outcome rather than a crash.

When only unrelated visual candidates are returned, the pipeline does not manufacture a social identity. This is intentional fail-closed behavior.

## T08–T10 — Hash integrity

The hash test established:

```text
Same evidence matches : True
Image changed          : True
URL changed            : True
```

This demonstrates that identical evidence is deterministic while changing either the image bytes or URL changes the SHA-256 fingerprint.

The stored `postHash` is calculated from:

```text
matched_url + downloaded_candidate_image_bytes
```

## T11 — Blockchain verification

The successful run stored and then read back:

```text
Face hash
Matched URL
Post fingerprint
Timestamp
Challenge nonce
```

All five values matched the locally expected values:

```text
✓ Face hash
✓ Social URL
✓ Post fingerprint
✓ Timestamp
✓ Challenge nonce
```

## Security observations

### Candidate ambiguity

A single social result can expose multiple image variants. Those variants are grouped under one social URL so they do not appear as multiple competing identities.

Broad directory/search URLs are filtered when a more specific profile/post is available.

### Fail-closed behavior

A high visual-search ranking alone is insufficient. Candidate acceptance requires the configured biometric/evidence checks.

### Credentials

`.env`, AWS credentials, wallet private keys, and personal test media must remain outside Git.

## Known limitations

1. Google Lens discovers publicly indexed image matches; it is not a dedicated biometric social-profile index.
2. Exact/near-duplicate image evidence is stronger than relying on generic visual-match ranking.
3. The local two-blink EAR mechanism is a prototype liveness check, not production PAD.
4. Similarity thresholds need calibration against a larger validation dataset before production deployment.
5. Polygon provides an integrity/audit record; it does not independently establish legal identity or account ownership.
6. The personal test image may not always be indexed or returned by a reverse-image engine; the correct behavior in that case is rejection rather than hardcoding.

## Screen-recording evidence

The final recording should show:

```text
1. t4.jpg — valid test
2. Liveness challenge
3. ~99.95% live/submitted AWS score
4. Specific LinkedIn result
5. Candidate biometric scores
6. Post hash
7. Polygon transaction
8. On-chain five-field verification
9. test2.jpeg — wrong-person rejection
10. t2.jpeg — three-face rejection
```
