import cv2

cap = cv2.VideoCapture(1, cv2.CAP_DSHOW)

print("Opened:", cap.isOpened())

if cap.isOpened():
    for _ in range(30):
        ret, frame = cap.read()

        if ret and frame is not None:
            print("Frame:", frame.shape)
            cv2.imshow("DroidCam - DSHOW 1", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

cap.release()
cv2.destroyAllWindows()
