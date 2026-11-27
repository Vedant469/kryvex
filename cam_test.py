import cv2

cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)

print("Camera opened:", cap.isOpened())

if not cap.isOpened():
    print("Camera failed to open.")
    exit()

print("Live preview starting... Press Q to quit.")

while True:
    ret, frame = cap.read()

    if not ret:
        print("Failed to capture frame.")
        break

    cv2.imshow("Kryvex - DroidCam Test", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
print("Camera test finished.")
