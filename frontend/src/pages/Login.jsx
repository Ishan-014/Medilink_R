import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  Mail,
  Lock,
  Eye,
  EyeOff,
  LogIn,
  Sparkles,
  ShieldCheck,
  Stethoscope,
  BriefcaseMedical,
  ScanLine,
  Camera,
  CheckCircle2,
  ArrowLeft,
} from "lucide-react";

const API = "http://127.0.0.1:8000";

function Login() {
  const navigate = useNavigate();

  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);

  const [step, setStep] = useState(1);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const [stage2Token, setStage2Token] = useState("");
  const [stage3Token, setStage3Token] = useState("");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [qrString, setQrString] = useState("");

  // FIX: React state so UI re-renders after camera starts
  const [cameraStarted, setCameraStarted] = useState(false);

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }

    setCameraStarted(false);
  };

  // ------------------------------------------------------------
  // STEP 1 — Credentials
  // ------------------------------------------------------------

  const loginCredentials = async (event) => {
    event.preventDefault();

    setLoading(true);
    setError("");

    try {
      const body = new URLSearchParams();

      body.append("username", email);
      body.append("password", password);

      const response = await fetch(
        `${API}/auth/login/credentials`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body,
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail || "Credential verification failed."
        );
      }

      setStage2Token(data.token);
      setStep(2);
    } catch (err) {
      setError(
        err.message ||
          "Unable to connect to MediLink backend."
      );
    } finally {
      setLoading(false);
    }
  };

  // ------------------------------------------------------------
  // STEP 2 — QR
  // ------------------------------------------------------------

  const verifyQR = async () => {
    setLoading(true);
    setError("");

    try {
      if (!qrString.trim()) {
        throw new Error(
          "Please scan or enter your ID card QR data."
        );
      }

      const body = new URLSearchParams();

      body.append("stage2_token", stage2Token);
      body.append("qr_string", qrString.trim());

      const response = await fetch(
        `${API}/auth/login/id-card`,
        {
          method: "POST",
          headers: {
            "Content-Type":
              "application/x-www-form-urlencoded",
          },
          body,
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail ||
            "ID card verification failed."
        );
      }

      setStage3Token(data.token);
      setStep(3);
    } catch (err) {
      setError(
        err.message ||
          "ID card verification failed."
      );
    } finally {
      setLoading(false);
    }
  };

  // ------------------------------------------------------------
  // STEP 3 — Face
  // ------------------------------------------------------------

  const startCamera = async () => {
    setError("");

    try {
      const stream =
        await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: "user",
          },
          audio: false,
        });

      streamRef.current = stream;

      // FIX: trigger React re-render
      setCameraStarted(true);

      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
    } catch (err) {
      console.error(err);

      setError(
        "Camera access was denied or unavailable."
      );
    }
  };

  const captureFace = async () => {
    if (
      !videoRef.current ||
      !canvasRef.current
    ) {
      setError("Camera is not ready.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const video = videoRef.current;
      const canvas = canvasRef.current;

      if (
        !video.videoWidth ||
        !video.videoHeight
      ) {
        throw new Error(
          "Camera is still starting. Please try again."
        );
      }

      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;

      const context =
        canvas.getContext("2d");

      context.drawImage(
        video,
        0,
        0,
        canvas.width,
        canvas.height
      );

      const blob =
        await new Promise((resolve) =>
          canvas.toBlob(
            resolve,
            "image/jpeg",
            0.9
          )
        );

      if (!blob) {
        throw new Error(
          "Could not capture camera frame."
        );
      }

      const file = new File(
        [blob],
        "face.jpg",
        {
          type: "image/jpeg",
        }
      );

      await verifyFace(file);
    } catch (err) {
      setError(
        err.message ||
          "Face capture failed."
      );

      setLoading(false);
    }
  };

  const verifyFace = async (file) => {
    try {
      const body = new FormData();

      body.append(
        "stage3_token",
        stage3Token
      );

      body.append("frame", file);

      const response = await fetch(
        `${API}/auth/login/face`,
        {
          method: "POST",
          body,
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail ||
            "Facial verification failed."
        );
      }

      // --------------------------------------------------------
      // FINAL JWT
      // --------------------------------------------------------

      localStorage.setItem(
        "access_token",
        data.token
      );

      // --------------------------------------------------------
      // GET AUTHENTICATED USER
      // --------------------------------------------------------

      const meResponse =
        await fetch(`${API}/me`, {
          headers: {
            Authorization:
              `Bearer ${data.token}`,
          },
        });

      const user =
        await meResponse.json();

      if (!meResponse.ok) {
        throw new Error(
          user.detail ||
            "Unable to load user profile."
        );
      }

      // --------------------------------------------------------
      // STORE USER
      // --------------------------------------------------------

      localStorage.setItem(
        "role",
        user.role
      );

      localStorage.setItem(
        "user_id",
        user.user_id
      );

      localStorage.setItem(
        "employee_id",
        user.employee_id
      );

      localStorage.setItem(
        "email",
        user.email
      );

      stopCamera();

      // --------------------------------------------------------
      // ROLE ROUTING
      // --------------------------------------------------------

      if (user.role === "doctor") {
        navigate("/doctor");
      } else if (
        user.role === "receptionist"
      ) {
        navigate("/receptionist");
      } else {
        throw new Error(
          `Unsupported role: ${user.role}`
        );
      }
    } catch (err) {
      setError(
        err.message ||
          "Authentication failed."
      );
    } finally {
      setLoading(false);
    }
  };

  // ------------------------------------------------------------
  // DEMO PRESETS
  // ------------------------------------------------------------

  const fillDoctor = () => {
    setEmail(
      "doctor@medilink.demo"
    );

    setPassword(
      "doctor123"
    );
  };

  const fillReceptionist = () => {
    setEmail(
      "reception@medilink.demo"
    );

    setPassword(
      "reception123"
    );
  };

  // ------------------------------------------------------------
  // BACK
  // ------------------------------------------------------------

  const goBack = () => {
    if (step === 3) {
      stopCamera();
    }

    setError("");

    if (step === 2) {
      setStep(1);
    } else if (step === 3) {
      setStep(2);
    }
  };

  return (
    <div className="login-page-new">
      <div className="login-panel">

        {/* HEADER */}

        <div className="login-logo">

          <div className="login-logo-mark">
            <Sparkles size={29} />
          </div>

          <h1>MediLink</h1>

          <p>
            Secure Healthcare Workflow Portal
          </p>

        </div>

        {/* PROGRESS */}

        <div
          style={{
            display: "flex",
            justifyContent: "center",
            gap: "10px",
            marginBottom: "25px",
          }}
        >
          {[1, 2, 3].map(
            (number) => (
              <div
                key={number}
                style={{
                  width: "32px",
                  height: "32px",
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: 600,
                  background:
                    step >= number
                      ? "#2563eb"
                      : "#e5e7eb",
                  color:
                    step >= number
                      ? "white"
                      : "#6b7280",
                }}
              >
                {step > number ? (
                  <CheckCircle2
                    size={18}
                  />
                ) : (
                  number
                )}
              </div>
            )
          )}
        </div>

        {/* ================================================== */}
        {/* STEP 1 */}
        {/* ================================================== */}

        {step === 1 && (
          <>
            <form
              onSubmit={
                loginCredentials
              }
              className="login-form-new"
            >

              <label>
                Work Email Address
              </label>

              <div className="input-with-icon">

                <Mail size={19} />

                <input
                  type="email"
                  value={email}
                  placeholder="doctor@medilink.demo"
                  onChange={(event) =>
                    setEmail(
                      event.target.value
                    )
                  }
                  required
                />

              </div>

              <label>
                Password
              </label>

              <div className="input-with-icon">

                <Lock size={19} />

                <input
                  type={
                    showPassword
                      ? "text"
                      : "password"
                  }
                  value={password}
                  placeholder="Enter password"
                  onChange={(event) =>
                    setPassword(
                      event.target.value
                    )
                  }
                  required
                />

                <button
                  type="button"
                  className="password-eye"
                  onClick={() =>
                    setShowPassword(
                      !showPassword
                    )
                  }
                >
                  {showPassword ? (
                    <EyeOff size={18} />
                  ) : (
                    <Eye size={18} />
                  )}
                </button>

              </div>

              <button
                className="primary-login-button"
                disabled={loading}
              >
                <LogIn size={20} />

                {loading
                  ? "Verifying..."
                  : "Continue"}
              </button>

            </form>

            <div className="preset-divider">
              <span>
                DEMO ACCESS
              </span>
            </div>

            <div className="preset-grid">

              <button
                onClick={fillDoctor}
                className="preset-card"
              >

                <div>

                  <span className="preset-role">
                    <Stethoscope
                      size={14}
                    />
                    Doctor
                  </span>

                  <strong>
                    doctor@medilink.demo
                  </strong>

                  <small>
                    Clinical EHR Access
                  </small>

                </div>

                <span>→</span>

              </button>

              <button
                onClick={
                  fillReceptionist
                }
                className="preset-card"
              >

                <div>

                  <span className="preset-role">

                    <BriefcaseMedical
                      size={14}
                    />

                    Receptionist

                  </span>

                  <strong>
                    reception@medilink.demo
                  </strong>

                  <small>
                    Admin & Scheduling Access
                  </small>

                </div>

                <span>→</span>

              </button>

            </div>
          </>
        )}

        {/* ================================================== */}
        {/* STEP 2 */}
        {/* ================================================== */}

        {step === 2 && (
          <div className="login-form-new">

            <button
              type="button"
              onClick={goBack}
              style={{
                border: "none",
                background: "none",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: "6px",
                marginBottom: "15px",
              }}
            >
              <ArrowLeft
                size={18}
              />

              Back
            </button>

            <div
              style={{
                textAlign: "center",
              }}
            >

              <ScanLine size={55} />

              <h2>
                Verify ID Card
              </h2>

              <p>
                Scan your employee ID
                card QR code.
              </p>

            </div>

            <label>
              QR String
            </label>

            <input
              value={qrString}
              onChange={(event) =>
                setQrString(
                  event.target.value
                )
              }
              placeholder="Scan QR or paste QR data"
            />

            <button
              type="button"
              className="primary-login-button"
              disabled={loading}
              onClick={verifyQR}
            >

              <ScanLine size={20} />

              {loading
                ? "Verifying..."
                : "Verify ID Card"}

            </button>

            <small
              style={{
                textAlign: "center",
              }}
            >
              Camera QR scanning will be
              connected next.
            </small>

          </div>
        )}

        {/* ================================================== */}
        {/* STEP 3 */}
        {/* ================================================== */}

        {step === 3 && (
          <div className="login-form-new">

            <button
              type="button"
              onClick={goBack}
              style={{
                border: "none",
                background: "none",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: "6px",
                marginBottom: "15px",
              }}
            >

              <ArrowLeft
                size={18}
              />

              Back

            </button>

            <div
              style={{
                textAlign: "center",
              }}
            >

              <Camera size={50} />

              <h2>
                Face Verification
              </h2>

              <p>
                Position your face inside
                the camera.
              </p>

            </div>

            {/* CAMERA */}

            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              style={{
                width: "100%",
                borderRadius: "14px",
                background: "#111827",
                minHeight: "260px",
                objectFit: "cover",
              }}
            />

            <canvas
              ref={canvasRef}
              style={{
                display: "none",
              }}
            />

            {/* START CAMERA */}

            {!cameraStarted && (
              <button
                type="button"
                className="primary-login-button"
                onClick={startCamera}
              >

                <Camera size={20} />

                Start Camera

              </button>
            )}

            {/* VERIFY FACE */}

            {cameraStarted && (
              <button
                type="button"
                className="primary-login-button"
                disabled={loading}
                onClick={captureFace}
              >

                <ShieldCheck
                  size={20}
                />

                {loading
                  ? "Verifying Face..."
                  : "Verify Face"}

              </button>
            )}

          </div>
        )}

        {/* ERROR */}

        {error && (
          <div className="login-error">
            {error}
          </div>
        )}

        {/* SECURITY */}

        <div className="login-security">

          <ShieldCheck size={16} />

          <span>
            3-Layer Authentication •
            RBAC Enabled
          </span>

        </div>

      </div>
    </div>
  );
}

export default Login;