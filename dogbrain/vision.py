"""
Vision: the dog's eyes.

- Pulls the ESP32-S3 vision module's MJPEG stream (reconnects on drops)
- YOLO object detection a few times a second
- Face detection + recognition with OpenCV's YuNet + SFace (no extra installs)
- "What do you see?" answers from a local vision-language model in Ollama
- Emits events when someone/something shows up

Enrol a face:  python -m dogbrain.vision enroll Chris
Watch:         python -m dogbrain.vision watch
"""
import base64
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

from .config import DATA, load

MODELS = DATA / "models"
FACES = DATA / "faces"
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
SFACE_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/"
             "face_recognition_sface_2021dec.onnx")


def _fetch(url: str) -> Path:
    MODELS.mkdir(parents=True, exist_ok=True)
    path = MODELS / url.rsplit("/", 1)[1]
    if not path.exists():
        print(f"Downloading {path.name}...")
        urllib.request.urlretrieve(url, path)
    return path


def side_of(cx: float, width: int) -> str:
    if cx < width / 3:
        return "left"
    if cx > 2 * width / 3:
        return "right"
    return "ahead"


class FaceBook:
    """Known faces: data/faces/<name>.npy holds that person's SFace embeddings."""

    def __init__(self, threshold: float):
        import cv2
        self.cv2 = cv2
        self.threshold = threshold
        self.detector = cv2.FaceDetectorYN.create(str(_fetch(YUNET_URL)), "", (320, 240), 0.8)
        self.recognizer = cv2.FaceRecognizerSF.create(str(_fetch(SFACE_URL)), "")
        self.people: dict[str, np.ndarray] = {}
        self.reload()

    def reload(self) -> None:
        FACES.mkdir(parents=True, exist_ok=True)
        self.people = {p.stem: np.load(p) for p in FACES.glob("*.npy")}

    def detect(self, frame) -> list:
        h, w = frame.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(frame)
        return [] if faces is None else list(faces)

    def embed(self, frame, face) -> np.ndarray:
        aligned = self.recognizer.alignCrop(frame, face)
        return self.recognizer.feature(aligned).flatten()

    def identify(self, emb: np.ndarray) -> tuple[str, float]:
        best, best_score = "someone", 0.0
        for name, embs in self.people.items():
            # cosine similarity, same as FaceRecognizerSF.match(..., FR_COSINE)
            sims = embs @ emb / (np.linalg.norm(embs, axis=1) * np.linalg.norm(emb) + 1e-9)
            score = float(sims.max())
            if score > best_score:
                best, best_score = name, score
        return (best, best_score) if best_score >= self.threshold else ("someone", best_score)

    def save(self, name: str, embs: list) -> Path:
        path = FACES / f"{name}.npy"
        old = [np.load(path)] if path.exists() else []
        np.save(path, np.vstack(old + [np.array(embs)]))
        self.reload()
        return path


class Vision:
    def __init__(self, cfg: dict, on_event=None):
        self.cfg = cfg["camera"]
        self.brain_cfg = cfg["brain"]
        self.on_event = on_event          # callback(kind, data)
        self.frame = None                 # latest BGR frame
        self.frame_time = 0.0
        self.objects: list[dict] = []     # [{label, conf, side}]
        self.faces: list[dict] = []       # [{name, score, side}]
        self.connected = False
        self._lock = threading.Lock()
        self._alive = False
        self._present: set[str] = set()   # labels/names currently in view (for events)
        self._absent_since: dict[str, float] = {}

    # ---- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._alive = True
        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._detect_loop, daemon=True).start()

    def stop(self) -> None:
        self._alive = False

    def _capture_loop(self) -> None:
        import cv2
        while self._alive:
            cap = cv2.VideoCapture(self.cfg["url"])
            if not cap.isOpened():
                self.connected = False
                time.sleep(2)
                continue
            self.connected = True
            while self._alive:
                ok, frame = cap.read()
                if not ok:
                    break
                with self._lock:
                    self.frame, self.frame_time = frame, time.time()
            cap.release()
            self.connected = False
            time.sleep(1)

    def _detect_loop(self) -> None:
        import torch
        from ultralytics import YOLO
        device = "mps" if torch.backends.mps.is_available() else (
            "cuda" if torch.cuda.is_available() else "cpu")
        model = YOLO(self.cfg["yolo_model"])
        faces = FaceBook(self.cfg["face_match"])
        period = 1.0 / max(1, self.cfg["fps"])
        while self._alive:
            t0 = time.time()
            frame = self.latest()
            if frame is not None:
                w = frame.shape[1]
                res = model.predict(frame, conf=self.cfg["conf"], device=device, verbose=False)[0]
                objs = []
                for b in res.boxes:
                    x1, _, x2, _ = b.xyxy[0].tolist()
                    objs.append({"label": model.names[int(b.cls)], "conf": round(float(b.conf), 2),
                                 "side": side_of((x1 + x2) / 2, w)})
                found = []
                for f in faces.detect(frame):
                    name, score = faces.identify(faces.embed(frame, f))
                    found.append({"name": name, "score": round(score, 2),
                                  "side": side_of(f[0] + f[2] / 2, w)})
                with self._lock:
                    self.objects, self.faces = objs, found
                self._emit_changes()
            time.sleep(max(0.0, period - (time.time() - t0)))

    def _emit_changes(self) -> None:
        now = time.time()
        seen = {o["label"] for o in self.objects} | {
            f"face:{f['name']}" for f in self.faces if f["name"] != "someone"}
        for key in seen - self._present:
            # only count as "arrived" if it was gone for at least 5 s (stops flicker)
            if now - self._absent_since.get(key, 0) > 5 and self.on_event:
                if key.startswith("face:"):
                    self.on_event("face_appeared", {"name": key[5:]})
                else:
                    self.on_event("object_appeared", {"label": key})
        for key in self._present - seen:
            self._absent_since[key] = now
        self._present = seen

    # ---- queries -------------------------------------------------------------
    def latest(self):
        with self._lock:
            if self.frame is None or time.time() - self.frame_time > 3:
                return None
            return self.frame.copy()

    def summary(self) -> str:
        if not self.connected:
            return "camera offline"
        with self._lock:
            objs, faces = list(self.objects), list(self.faces)
        parts = [f"{f['name']} ({f['side']})" for f in faces if f["name"] != "someone"]
        named_people = len(parts)
        people = [o for o in objs if o["label"] == "person"]
        if len(people) > named_people:
            parts.append(f"{len(people) - named_people} unknown person(s)")
        parts += [f"{o['label']} ({o['side']})" for o in objs if o["label"] != "person"]
        return ", ".join(parts) if parts else "nothing notable"

    def sees(self, target: str) -> str | None:
        """Return the side ('left'/'ahead'/'right') where target is visible, else None."""
        t = target.lower().strip()
        with self._lock:
            for f in self.faces:
                if f["name"].lower() == t:
                    return f["side"]
            for o in self.objects:
                if o["label"].lower() == t or (t in ("someone", "a person", "people") and
                                                o["label"] == "person"):
                    return o["side"]
        return None

    def describe(self, question: str = "") -> str:
        """Ask the local vision-language model about the current view."""
        import cv2
        import ollama
        frame = self.latest()
        if frame is None:
            return "I can't see anything right now; my camera isn't connected."
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        prompt = (question or "What do you see?") + (
            " Answer in one or two short spoken sentences. "
            f"Known detections for context: {self.summary()}.")
        client = ollama.Client(host=self.brain_cfg["ollama_host"])
        r = client.chat(model=self.brain_cfg["vision_model"], messages=[{
            "role": "user", "content": prompt,
            "images": [base64.b64encode(jpg.tobytes()).decode()]}])
        return r["message"]["content"].strip()

    def enroll(self, name: str, shots: int = 15) -> str:
        """Capture several face embeddings of `name` from the live camera."""
        fb = FaceBook(self.cfg["face_match"])
        embs, deadline = [], time.time() + 30
        while len(embs) < shots and time.time() < deadline:
            frame = self.latest()
            if frame is not None:
                faces = fb.detect(frame)
                if len(faces) == 1:          # only enrol when exactly one face is in view
                    embs.append(fb.embed(frame, faces[0]))
                    print(f"  got {len(embs)}/{shots} - turn your head a little")
            time.sleep(0.4)
        if len(embs) < 3:
            return f"Only got {len(embs)} good shots of {name}; try again with better light."
        path = fb.save(name, embs)
        return f"Saved {len(embs)} shots of {name} to {path}"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["watch", "enroll", "describe"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--config")
    args = ap.parse_args()

    v = Vision(load(args.config), on_event=lambda k, d: print(f"[event] {k} {d}"))
    v.start()
    print("Connecting to camera...")
    for _ in range(50):
        if v.latest() is not None:
            break
        time.sleep(0.2)
    if args.cmd == "enroll":
        if not args.name:
            raise SystemExit("usage: python -m dogbrain.vision enroll <name>")
        print(f"Look at the dog, {args.name}.")
        print(v.enroll(args.name))
    elif args.cmd == "describe":
        print(v.describe(args.name or ""))
    else:
        last = ""
        while True:
            s = v.summary()
            if s != last:
                print(time.strftime("%H:%M:%S"), s)
                last = s
            time.sleep(0.5)


if __name__ == "__main__":
    main()
