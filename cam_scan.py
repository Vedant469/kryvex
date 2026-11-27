import cv2

for i in range(10):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)

    if cap.isOpened():
        ret, frame = cap.read()

        if ret:
            print(f"\n[FOUND] Camera index: {i}")
            print("Press N = next camera | Q = quit")

            while True:
                ret, frame = cap.read()

                if not ret:
                    break

                cv2.imshow(f"Camera Index {i}", frame)
                key = cv2.waitKey(1) & 0xFF

                if key == ord('n'):
                    break
                elif key == ord('q'):
                    cap.release()
                    cv2.destroyAllWindows()
                    exit()

        cap.release()
        cv2.destroyAllWindows()

print("Camera scan completed.")
