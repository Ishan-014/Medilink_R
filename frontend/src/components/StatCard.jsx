function StatCard({
    label,
    value,
    subtitle,
    icon,
    footer
  }) {
    return (
      <div className="stat-card">
  
        <div className="stat-card-top">
  
          <div>
            <span className="stat-label">
              {label}
            </span>
  
            <div className="stat-value">
              {value}
            </div>
          </div>
  
          <div className="stat-icon">
            {icon}
          </div>
  
        </div>
  
        {subtitle && (
          <div className="stat-subtitle">
            {subtitle}
          </div>
        )}
  
        {footer && (
          <div className="stat-footer">
            {footer}
          </div>
        )}
  
      </div>
    );
  }
  
  export default StatCard;