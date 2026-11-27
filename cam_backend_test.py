import cv2

backends = [
    ("DSHOW", cv2.CAP_DSHOW),
    ("MSMF", cv2.CAP_MSMF)
]

for backend_name, backend in backends:
    print(f"\n--- Testing {backend_name} ---")

    for i in range(5):
        cap = cv2.VideoCapture(i, backend)

        if cap.isOpened():
            ret, frame = cap.read()

            if ret:
                print(f"[OK] {backend_name} index {i} | {frame.shape}")
                cv2.imshow(f"{backend_name} Camera {i}", frame)
                cv2.waitKey(2000)
                cv2.destroyAllWindows()

            cap.release()

print("\nScan complete.")
