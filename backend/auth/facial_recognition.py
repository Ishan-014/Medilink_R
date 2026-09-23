"""
backend/auth/facial_recognition.py

Layer 3 of the login pipeline: facial recognition.

Library choice: `deepface` (pip install deepface) rather than
`face_recognition` (dlib-based). dlib requires compiling C++ from source,
which is slow/fragile to install on Windows and took too long even in this
sandbox -- deepface installs cleanly via pip (it wraps TensorFlow/Keras
models) and is a better fit for a demo you need working quickly.

Flow:
  1. (provisioning time) enroll_face() -- store one reference embedding per
     employee, computed from a clear reference photo.
  2. (login time) verify_face() -- compute an embedding from a freshly
     captured frame and compare it to the stored reference.

NOTE: deepface's `verify()` does the embedding + comparison in one call and
handles model loading/caching internally, so we don't need to manage raw
128/512-d vectors by hand for this demo.
"""

import os
import json
from deepface import DeepFace

from .models import StageToken, LoginStage, LoginResult
from .credentials import verify_stage_token

# Face embeddings model. "Facenet" is a good accuracy/speed balance for a demo.
MODEL_NAME = "Facenet"
DISTANCE_THRESHOLD = 0.40   # lower = stricter match; tune against your test photos

# "mtcnn" instead of "opencv" -- avoids a flaky Windows packaging issue where
# opencv-python doesn't reliably ship its haarcascade_frontalface_default.xml
# file. mtcnn is a pure Python/TF detector already installed as a deepface
# dependency, and is more accurate than the Haar cascade besides.
DETECTOR_BACKEND = "mtcnn"

REFERENCE_PHOTOS_DIR = os.environ.get("MEDILINK_FACE_DIR", "./data/reference_faces")
os.makedirs(REFERENCE_PHOTOS_DIR, exist_ok=True)


def _reference_photo_path(employee_id: str) -> str:
    return os.path.join(REFERENCE_PHOTOS_DIR, f"{employee_id}.jpg")


# ---------------------------------------------------------------------------
# Provisioning-time: enroll an employee's reference face
# ---------------------------------------------------------------------------
def enroll_face(employee_id: str, reference_image_path: str) -> None:
    """Copies/validates a clear, front-facing reference photo for this
    employee. Raises if no face is detected in the provided image."""
    # This call forces face detection to run once, failing fast if the
    # photo doesn't actually contain a usable face.
    DeepFace.extract_faces(img_path=reference_image_path, detector_backend=DETECTOR_BACKEND)

    dest = _reference_photo_path(employee_id)
    with open(reference_image_path, "rb") as src, open(dest, "wb") as out:
        out.write(src.read())


# ---------------------------------------------------------------------------
# Login-time: verify a freshly captured frame against the stored reference
# ---------------------------------------------------------------------------
def verify_face(stage3_token: str, captured_image_path: str) -> LoginResult:
    try:
        stage_token: StageToken = verify_stage_token(stage3_token, LoginStage.STAGE3_FACIAL)
    except Exception:
        return LoginResult(success=False, stage=LoginStage.STAGE3_FACIAL,
                            message="Session expired or invalid. Please log in again.")

    employee_id = stage_token.employee_id   # captured now, before the slow model call below --
                                             # the stage3 token may expire during DeepFace.verify()
                                             # on a cold model load, so we don't re-decode it later.
    reference_path = _reference_photo_path(employee_id)
    if not os.path.exists(reference_path):
        return LoginResult(success=False, stage=LoginStage.STAGE3_FACIAL,
                            message="No enrolled face on file for this employee.")

    try:
        result = DeepFace.verify(
            img1_path=captured_image_path,
            img2_path=reference_path,
            model_name=MODEL_NAME,
            detector_backend=DETECTOR_BACKEND,
            distance_metric="cosine",
        )
    except ValueError:
        # deepface raises this when no face is detected in one of the images
        return LoginResult(success=False, stage=LoginStage.STAGE3_FACIAL,
                            message="No face detected in captured frame. Please try again.")

    if not result["verified"] or result["distance"] > DISTANCE_THRESHOLD:
        return LoginResult(success=False, stage=LoginStage.STAGE3_FACIAL,
                            message="Face did not match employee record.")

    # All three layers passed -- caller (router.py) issues the real access token here.
    # employee_id is returned directly (captured before the slow DeepFace call above)
    # rather than making router.py re-decode stage3_token, which could have expired
    # during that call.
    return LoginResult(success=True, stage=LoginStage.COMPLETE,
                        message="Facial recognition verified. Login complete.",
                        employee_id=employee_id)