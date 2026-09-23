import {
    LayoutDashboard,
    Users,
    BrainCircuit,
    CalendarDays,
    LogOut,
    Sparkles
  } from "lucide-react";
  
  import {
    useLocation,
    useNavigate
  } from "react-router-dom";
  
  
  function Sidebar() {
    const navigate = useNavigate();
    const location = useLocation();
  
    const role = localStorage.getItem("role");
  
    const base =
      role === "doctor"
        ? "/doctor"
        : "/receptionist";
  
  
    const logout = () => {
      localStorage.clear();
      navigate("/");
    };
  
  
    const dashboardActive =
      location.pathname === base &&
      !location.hash;
  
    const patientsActive =
      location.pathname.includes("/patient/") ||
      location.hash === "#patients";
  
    const aiActive =
      location.pathname === "/ai";
  
    const appointmentsActive =
      location.hash === "#appointments";
  
  
    return (
      <aside className="sidebar">
  
        <div className="brand" onClick={() => navigate(base)}>
          <div className="brand-icon">
            <Sparkles size={19} strokeWidth={2.2} />
          </div>
  
          <div>
            <div className="brand-name">MediLink</div>
            <div className="brand-subtitle">HEALTHCARE</div>
          </div>
        </div>
  
  
        <nav className="sidebar-nav">
  
          <button
            className={`sidebar-link ${dashboardActive ? "active" : ""}`}
            onClick={() => navigate(base)}
          >
            <LayoutDashboard size={18} />
            <span>Dashboard</span>
          </button>
  
  
          <button
            className={`sidebar-link ${patientsActive ? "active" : ""}`}
            onClick={() => navigate(`${base}#patients`)}
          >
            <Users size={18} />
            <span>Patients</span>
          </button>
  
  
          <button
            className={`sidebar-link ${aiActive ? "active" : ""}`}
            onClick={() => navigate("/ai")}
          >
            <BrainCircuit size={18} />
            <span>MediLink AI</span>
          </button>
  
  
          <button
            className={`sidebar-link ${appointmentsActive ? "active" : ""}`}
            onClick={() => navigate(`${base}#appointments`)}
          >
            <CalendarDays size={18} />
            <span>Appointments</span>
          </button>
  
        </nav>
  
  
        <div className="sidebar-bottom">
  
          <div className="portal-card">
  
            <div className="portal-status">
              <span className="online-dot"></span>
  
              <div>
                <span className="tiny-label">ACTIVE PORTAL</span>
  
                <strong>
                  {role === "doctor"
                    ? "Physician Duty"
                    : "Reception Desk"}
                </strong>
              </div>
            </div>
  
            <span className="role-pill">
              {role === "doctor"
                ? "Doctor"
                : "Receptionist"}
            </span>
  
          </div>
  
  
          <button
            className="signout-button"
            onClick={logout}
          >
            <LogOut size={17} />
            Sign Out
          </button>
  
        </div>
  
      </aside>
    );
  }
  
  export default Sidebar;