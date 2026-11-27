import cv2

CAMERA_INDEX = 1

cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_MSMF)

print("Camera opened:", cap.isOpened())

while cap.isOpened():
    ret, frame = cap.read()

    if not ret:
        print("Frame read failed")
        break

    cv2.imshow("DroidCam Live Test", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()