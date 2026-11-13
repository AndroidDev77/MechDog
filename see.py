"""
see.py - MechDog "eyes" test (Phase 2).

Pulls the ESP32-S3 vision module's MJPEG stream and runs YOLO on it,
using the Mac's GPU (Apple Silicon / Metal) when available.

Usage:
    python see.py --cam 192.168.1.50          # camera on your home Wi-Fi
    python see.py --cam 192.168.5.1           # camera's own hotspot (AP mode)
    python see.py --cam 192.168.1.50 --show   # also open a preview window

Prints lines like:  person 0.91 left   |   cup 0.62 center
"""
import argparse
import time

import cv2
import torch
from ultralytics import YOLO


def side_of(cx: float, width: int) -> str:
    if cx < width / 3:
        return "left"
    if cx > 2 * width / 3:
        return "right"
    return "center"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", required=True, help="camera IP address")
    ap.add_argument("--port", default=81, type=int)
    ap.add_argument("--model", default="yolo11n.pt", help="yolo11n.pt (fast) or yolo11s.pt (better)")
    ap.add_argument("--conf", default=0.45, type=float)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Using device: {device}")
    model = YOLO(args.model)  # downloads weights on first run

    url = f"http://{args.cam}:{args.port}/stream"
    last_seen = ""

    while True:  # reconnect loop: the stream drops when Wi-Fi blips
        cap = cv2.VideoCapture(url)
        if not cap.isOpened():
            print(f"Can't open {url}, retrying in 2 s...")
            time.sleep(2)
            continue
        print(f"Connected to {url}")

        while True:
            ok, frame = cap.read()
            if not ok:
                print("Stream dropped, reconnecting...")
                break

            result = model.predict(frame, conf=args.conf, device=device, verbose=False)[0]
            width = frame.shape[1]
            seen = []
            for box in result.boxes:
                label = model.names[int(box.cls)]
                conf = float(box.conf)
                x1, _, x2, _ = box.xyxy[0].tolist()
                seen.append(f"{label} {conf:.2f} {side_of((x1 + x2) / 2, width)}")

            line = "  |  ".join(seen) or "(nothing)"
            if line != last_seen:  # only print when the scene changes
                print(time.strftime("%H:%M:%S"), line)
                last_seen = line

            if args.show:
                cv2.imshow("MechDog", result.plot())
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    return

        cap.release()
        time.sleep(1)


if __name__ == "__main__":
    main()
