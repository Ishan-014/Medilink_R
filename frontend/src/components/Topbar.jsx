import {
    Search,
    Bell,
    Clock3
  } from "lucide-react";
  
  
  function Topbar() {
    const fullName =
      localStorage.getItem("full_name") ||
      "MediLink User";
  
    const role =
      localStorage.getItem("role");
  
  
    const initials = fullName
      .split(" ")
      .map((part) => part[0])
      .join("")
      .slice(0, 2)
      .toUpperCase();
  
  
    return (
      <header className="topbar-new">
  
        <div className="breadcrumbs">
          <span>MediLink</span>
          <span className="breadcrumb-divider">/</span>
          <strong>
            {role === "doctor"
              ? "Clinical Workspace"
              : "Administrative Workspace"}
          </strong>
        </div>
  
  
        <div className="topbar-actions">
  
          <button className="icon-button">
            <Search size={18} />
          </button>
  
          <button className="icon-button">
            <Bell size={18} />
          </button>
  
          <button className="icon-button">
            <Clock3 size={18} />
          </button>
  
  
          <div className="topbar-divider"></div>
  
  
          <div className="user-avatar">
            {initials}
          </div>
  
  
          <div className="topbar-user">
            <strong>{fullName}</strong>
  
            <span>
              {role === "doctor"
                ? "Clinical User"
                : "Administrative User"}
            </span>
          </div>
  
  
          <span className="top-role">
            {role === "doctor"
              ? "Clinical Access"
              : "Admin Access"}
          </span>
  
        </div>
  
      </header>
    );
  }
  
  export default Topbar;