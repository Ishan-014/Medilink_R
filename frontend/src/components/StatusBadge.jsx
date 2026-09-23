function StatusBadge({
    children,
    tone = "neutral"
  }) {
    return (
      <span className={`status-badge ${tone}`}>
        {children}
      </span>
    );
  }
  
  export default StatusBadge;