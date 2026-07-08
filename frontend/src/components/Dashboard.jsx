import { AlertTriangle, CircleDollarSign, Database, RefreshCw, Zap } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import BurnChart from './BurnChart.jsx';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

function formatNumber(value) {
  return new Intl.NumberFormat('en', { maximumFractionDigits: 0 }).format(value || 0);
}

function formatCompact(value) {
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value || 0);
}

function formatCurrency(value, currency = 'USD') {
  return new Intl.NumberFormat('en', { style: 'currency', currency: currency.toUpperCase() }).format(value || 0);
}

function pivotTrendRows(rows) {
  const buckets = new Map();
  const series = new Set();

  rows.forEach((row) => {
    const key = row.time_bucket;
    const dimension = row.dimension || row.provider || 'unknown';
    series.add(dimension);
    if (!buckets.has(key)) buckets.set(key, { time_bucket: key });
    const bucket = buckets.get(key);
    bucket[dimension] = (bucket[dimension] || 0) + (row.tokens || 0);
  });

  return {
    chartData: Array.from(buckets.values()),
    series: Array.from(series).sort(),
  };
}

function StatCard({ icon: Icon, label, value, detail }) {
  return (
    <div className="stat-card">
      <div className="stat-icon">
        <Icon size={19} />
      </div>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
        {detail ? <span>{detail}</span> : null}
      </div>
    </div>
  );
}

function RateLimitPanel({ limits }) {
  if (!limits?.length) {
    return (
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>Observed capacity</h2>
            <p>No rate-limit snapshots yet. Enable canary mode or keep demo data on.</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>Observed capacity</h2>
          <p>Latest provider response headers. Treat these as snapshots, not contractual limits.</p>
        </div>
      </div>
      <div className="limit-list">
        {limits.map((limit) => {
          const pct = limit.limit_value ? Math.max(0, Math.min(100, (limit.remaining / limit.limit_value) * 100)) : 0;
          return (
            <div className="limit-row" key={`${limit.provider}-${limit.limit_type}`}>
              <div>
                <strong>{limit.provider}</strong>
                <span>{limit.limit_type}</span>
              </div>
              <div className="meter" aria-label={`${limit.provider} ${limit.limit_type} remaining`}>
                <span style={{ width: `${pct}%` }} />
              </div>
              <div className="limit-values">
                {formatCompact(limit.remaining)} / {formatCompact(limit.limit_value)}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function AlertPanel({ alerts }) {
  if (!alerts?.length) {
    return (
      <section className="panel alert-panel quiet">
        <div className="section-heading">
          <div>
            <h2>Token alerts</h2>
            <p>No active alerts. Threshold defaults to 95% usage.</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="panel alert-panel hot">
      <div className="section-heading">
        <div>
          <h2>Token alerts</h2>
          <p>Active threshold breaches. Check provider limits before pushing more workload.</p>
        </div>
      </div>
      <div className="alert-list">
        {alerts.map((alert) => (
          <div className="alert-row" key={alert.alert_key}>
            <AlertTriangle size={20} />
            <div>
              <strong>{alert.message}</strong>
              <span>
                {formatCompact(alert.used_tokens)} of {formatCompact(alert.token_limit)} tokens used
                {' '}({Math.round((alert.usage_ratio || 0) * 100)}%)
              </span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function Dashboard() {
  const [summary, setSummary] = useState(null);
  const [metadata, setMetadata] = useState({ providers: [], projects: [], models: [] });
  const [trendRows, setTrendRows] = useState([]);
  const [filters, setFilters] = useState({ days: 7, groupBy: 'provider', provider: 'all', project: 'all' });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadData = useCallback(async () => {
    setError('');
    setLoading(true);
    try {
      const trendParams = new URLSearchParams({
        days: String(filters.days),
        group_by: filters.groupBy,
        provider: filters.provider,
        project: filters.project,
      });
      const [summaryRes, metadataRes, trendsRes] = await Promise.all([
        fetch(`${API_BASE}/api/analytics/summary`),
        fetch(`${API_BASE}/api/analytics/projects`),
        fetch(`${API_BASE}/api/analytics/trends?${trendParams}`),
      ]);
      if (!summaryRes.ok || !metadataRes.ok || !trendsRes.ok) {
        throw new Error('API returned a non-200 response');
      }
      setSummary(await summaryRes.json());
      setMetadata(await metadataRes.json());
      setTrendRows(await trendsRes.json());
    } catch (err) {
      setError(err.message || 'Failed to load dashboard data');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const chart = useMemo(() => pivotTrendRows(trendRows), [trendRows]);
  const tokens24h = useMemo(
    () => (summary?.burn_24h || []).reduce((total, row) => total + (row.total_tokens || 0), 0),
    [summary]
  );
  const requests24h = useMemo(
    () => (summary?.burn_24h || []).reduce((total, row) => total + (row.request_count || 0), 0),
    [summary]
  );
  const monthlyCost = useMemo(
    () => (summary?.cost_month || []).reduce((total, row) => total + (row.amount || 0), 0),
    [summary]
  );
  const activeAlerts = summary?.active_alerts || [];

  const providerOptions = metadata.providers.map((item) => item.provider);
  const projectOptions = metadata.projects.map((item) => item.project_id);

  return (
    <main className="dashboard">
      <header className="topbar">
        <div>
          <span className="eyebrow">Token Dashy</span>
          <h1>AI token analytics</h1>
          <p>Local SQLite burn tracking for OpenAI and Anthropic usage, cost, and observed rate-limit capacity.</p>
        </div>
        <button className="primary-action" onClick={loadData} disabled={loading}>
          <RefreshCw size={17} />
          Refresh
        </button>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="stats-grid" aria-label="Dashboard metrics">
        <StatCard icon={Zap} label="24h token burn" value={formatCompact(tokens24h)} detail={`${formatNumber(requests24h)} requests`} />
        <StatCard icon={CircleDollarSign} label="Month cost" value={formatCurrency(monthlyCost)} detail="provider cost APIs" />
        <StatCard
          icon={Database}
          label="Tracked projects"
          value={formatNumber(summary?.totals?.tracked_projects)}
          detail={`${formatCompact(summary?.totals?.all_time_tokens)} all-time tokens`}
        />
        <StatCard icon={AlertTriangle} label="Active alerts" value={formatNumber(activeAlerts.length)} detail="95% token threshold" />
      </section>

      <section className="filters" aria-label="Dashboard filters">
        <label>
          Window
          <select value={filters.days} onChange={(event) => setFilters({ ...filters, days: Number(event.target.value) })}>
            <option value={1}>24 hours</option>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
          </select>
        </label>
        <label>
          Group by
          <select value={filters.groupBy} onChange={(event) => setFilters({ ...filters, groupBy: event.target.value })}>
            <option value="provider">Provider</option>
            <option value="project">Project</option>
            <option value="model">Model</option>
          </select>
        </label>
        <label>
          Provider
          <select value={filters.provider} onChange={(event) => setFilters({ ...filters, provider: event.target.value })}>
            <option value="all">All providers</option>
            {providerOptions.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
        </label>
        <label>
          Project
          <select value={filters.project} onChange={(event) => setFilters({ ...filters, project: event.target.value })}>
            <option value="all">All projects</option>
            {projectOptions.map((project) => (
              <option key={project} value={project}>
                {project}
              </option>
            ))}
          </select>
        </label>
      </section>

      {loading && !summary ? <div className="loading">Loading dashboard data...</div> : null}

      <div className="content-grid">
        <BurnChart data={chart.chartData} series={chart.series} />
        <div className="side-stack">
          <AlertPanel alerts={activeAlerts} />
          <RateLimitPanel limits={summary?.rate_limits || []} />
        </div>
      </div>

      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>Project burn</h2>
            <p>Projects map to OpenAI project IDs/API keys or Anthropic workspaces.</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Project</th>
                <th>Tokens</th>
                <th>Requests</th>
              </tr>
            </thead>
            <tbody>
              {metadata.projects.map((project) => (
                <tr key={`${project.provider}-${project.project_id}`}>
                  <td>{project.provider}</td>
                  <td>{project.project_id}</td>
                  <td>{formatNumber(project.total_tokens)}</td>
                  <td>{formatNumber(project.request_count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
