import {
    useState
  } from "react";
  
  import {
    useNavigate
  } from "react-router-dom";
  
  import {
    Mail,
    Lock,
    Eye,
    EyeOff,
    LogIn,
    Sparkles,
    ShieldCheck,
    Stethoscope,
    BriefcaseMedical
  } from "lucide-react";
  
  
  function Login() {
    const navigate =
      useNavigate();
  
  
    const [email, setEmail] =
      useState("");
  
    const [password, setPassword] =
      useState("");
  
    const [showPassword, setShowPassword] =
      useState(false);
  
    const [loading, setLoading] =
      useState(false);
  
    const [error, setError] =
      useState("");
  
  
    const login =
      async (event) => {
        event.preventDefault();
  
        setLoading(true);
        setError("");
  
  
        try {
          const response =
            await fetch(
              "http://127.0.0.1:8000/login",
              {
                method: "POST",
  
                headers: {
                  "Content-Type":
                    "application/json"
                },
  
                body:
                  JSON.stringify({
                    email,
                    password
                  })
              }
            );
  
  
          const data =
            await response.json();
  
  
          if (!response.ok) {
            setError(
              data.detail ||
              "Login failed"
            );
  
            return;
          }
  
  
          localStorage.setItem(
            "access_token",
            data.access_token
          );
  
          localStorage.setItem(
            "role",
            data.user.role
          );
  
          localStorage.setItem(
            "full_name",
            data.user.full_name
          );
  
  
          navigate(
            data.user.role === "doctor"
              ? "/doctor"
              : "/receptionist"
          );
  
        } catch {
          setError(
            "Unable to connect to MediLink backend."
          );
        } finally {
          setLoading(false);
        }
      };
  
  
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
  
  
    return (
      <div className="login-page-new">
  
        <div className="login-panel">
  
          <div className="login-logo">
  
            <div className="login-logo-mark">
              <Sparkles
                size={29}
              />
            </div>
  
            <h1>MediLink</h1>
  
            <p>
              Healthcare Workflow Portal • Role-Based Access Control
            </p>
  
          </div>
  
  
          <form
            onSubmit={login}
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
  
  
            <div className="password-label">
              <label>
                Password
              </label>
            </div>
  
  
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
                {showPassword
                  ? <EyeOff size={18} />
                  : <Eye size={18} />}
              </button>
  
            </div>
  
  
            <div className="login-options">
  
              <label className="remember-row">
                <input type="checkbox" />
                Remember this workstation
              </label>
  
              <span className="ready-badge">
                <span className="online-dot"></span>
                Demo Environment Ready
              </span>
  
            </div>
  
  
            <button
              className="primary-login-button"
              disabled={loading}
            >
              <LogIn size={20} />
  
              {loading
                ? "Signing In..."
                : "Sign In to MediLink"}
            </button>
  
  
            {error && (
              <div className="login-error">
                {error}
              </div>
            )}
  
          </form>
  
  
          <div className="preset-divider">
            <span>
              SIMULATION PRESETS
            </span>
          </div>
  
  
          <div className="preset-title">
            Select quick-fill demo access:
          </div>
  
  
          <div className="preset-grid">
  
            <button
              onClick={fillDoctor}
              className="preset-card"
            >
              <div>
                <span className="preset-role">
                  <Stethoscope size={14} />
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
  
  
          <div className="login-security">
  
            <ShieldCheck size={16} />
  
            <span>
              RBAC Enabled • Synthetic EHR Data • University Project
            </span>
  
          </div>
  
        </div>
  
      </div>
    );
  }
  
  export default Login;