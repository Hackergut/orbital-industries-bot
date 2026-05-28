import { useEffect, useState, useRef } from 'react'
import axios from 'axios'

const API = process.env.NEXT_PUBLIC_API_URL || ''

export default function Dashboard() {
  const [data, setData] = useState({
    status: 'Connecting...',
    latest_screenshot: null,
    logs: [],
    submitted: [],
    submitted_count: 0,
    processed_count: 0,
    failed_count: 0,
    rate_per_hour: 0,
  })
  const [connected, setConnected] = useState(false)
  const [lastUpdate, setLastUpdate] = useState(null)
  const evtSourceRef = useRef(null)

  useEffect(() => {
    // Try SSE first
    const connectSSE = () => {
      if (evtSourceRef.current) {
        evtSourceRef.current.close()
      }
      try {
        const evtSource = new EventSource(`${API}/api/live/all`)
        evtSourceRef.current = evtSource

        evtSource.onmessage = (event) => {
          try {
            const parsed = JSON.parse(event.data)
            setData(prev => ({
              ...prev,
              ...parsed,
              logs: parsed.logs || prev.logs,
              submitted: parsed.submitted || prev.submitted,
            }))
            setConnected(true)
            setLastUpdate(new Date())
          } catch (e) {
            console.error('SSE parse error:', e)
          }
        }

        evtSource.onerror = () => {
          setConnected(false)
          evtSource.close()
        }
      } catch (e) {
        console.error('SSE not supported, falling back to polling')
        setConnected(false)
      }
    }

    connectSSE()

    // Fallback polling every 3s if SSE not connected
    const pollInterval = setInterval(async () => {
      if (evtSourceRef.current && evtSourceRef.current.readyState === EventSource.OPEN) {
        return
      }
      try {
        const res = await axios.get(`${API}/dashboard/api/status`, { timeout: 5000 })
        setData(prev => ({
          ...prev,
          ...res.data,
          logs: res.data.logs || prev.logs,
          submitted: res.data.submitted || prev.submitted,
        }))
        setConnected(true)
        setLastUpdate(new Date())
      } catch (e) {
        setConnected(false)
        console.error('API error:', e)
      }
    }, 3000)

    return () => {
      clearInterval(pollInterval)
      if (evtSourceRef.current) {
        evtSourceRef.current.close()
      }
    }
  }, [])

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div style={styles.brand}>
          <div style={styles.dot} />
          <h1>Orbital Real-Time</h1>
        </div>
        <div style={styles.connectionBadge(connected)}>
          {connected ? '● Live' : '○ Reconnecting...'}
        </div>
      </div>

      <div style={styles.statsGrid}>
        <StatCard label="Status" value={data.status} accent="#00ff88" />
        <StatCard label="Submitted" value={data.submitted_count} accent="#00ccff" />
        <StatCard label="Processed" value={data.processed_count} accent="#ffaa00" />
        <StatCard label="Failed" value={data.failed_count || 0} accent="#ff3333" />
        <StatCard label="Rate/hr" value={data.rate_per_hour || 0} accent="#aa66ff" />
        <StatCard label="Last Update" value={lastUpdate ? lastUpdate.toLocaleTimeString() : '--'} accent="#888" />
      </div>

      <div style={styles.grid}>
        <div style={styles.card}>
          <h2 style={styles.cardTitle}>📸 Latest Screenshot</h2>
          <div style={styles.imgWrap}>
            {data.latest_screenshot ? (
              <img
                src={`${API}/static/screenshots/${data.latest_screenshot}?t=${Date.now()}`}
                alt="Latest"
                style={styles.img}
                onError={(e) => { e.target.style.display = 'none' }}
              />
            ) : (
              <div style={styles.placeholder}>Waiting for screenshots...</div>
            )}
          </div>
        </div>

        <div style={styles.card}>
          <h2 style={styles.cardTitle}>📝 Live Logs</h2>
          <div style={styles.logs}>
            {data.logs.slice(-40).map((line, i) => (
              <div key={i} style={styles.logLine(line)}>{line}</div>
            ))}
          </div>
        </div>
      </div>

      <div style={styles.card}>
        <h2 style={styles.cardTitle}>✅ Recent Submissions</h2>
        <div style={styles.submissions}>
          {data.submitted.length === 0 && <p style={styles.muted}>No submissions yet.</p>}
          {data.submitted.slice(-20).map((s, i) => (
            <div key={i} style={styles.submissionRow}>
              <span style={styles.submissionIndex}>{i + 1}</span>
              <span style={styles.submissionDomain}>{s.domain}</span>
              <span style={styles.submissionFields}>{s.fields_filled}/{s.fields_total}</span>
              <span style={styles.submissionStatus(s.status)}>{s.status}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, accent }) {
  return (
    <div style={styles.statCard}>
      <div style={{ ...styles.statValue, color: accent }}>{value}</div>
      <div style={styles.statLabel}>{label}</div>
    </div>
  )
}

const styles = {
  container: {
    padding: '24px',
    fontFamily: 'system-ui, -apple-system, sans-serif',
    background: '#0a0e27',
    color: '#e0e0e0',
    minHeight: '100vh',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '24px',
    flexWrap: 'wrap',
    gap: '12px',
  },
  brand: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  dot: {
    width: '12px',
    height: '12px',
    borderRadius: '50%',
    background: '#00ff88',
    boxShadow: '0 0 8px #00ff88',
  },
  connectionBadge: (connected) => ({
    padding: '6px 12px',
    borderRadius: '6px',
    fontSize: '13px',
    fontWeight: 'bold',
    background: connected ? '#004d00' : '#4d0000',
    color: connected ? '#00ff88' : '#ff3333',
    border: `1px solid ${connected ? '#00ff88' : '#ff3333'}`,
  }),
  statsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
    gap: '12px',
    marginBottom: '24px',
  },
  statCard: {
    background: '#1a1e3f',
    border: '1px solid #333',
    borderRadius: '8px',
    padding: '14px',
    textAlign: 'center',
  },
  statValue: {
    fontSize: '1.6em',
    fontWeight: 'bold',
    marginBottom: '4px',
  },
  statLabel: {
    fontSize: '11px',
    color: '#888',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '20px',
    marginBottom: '20px',
  },
  card: {
    background: '#1a1e3f',
    border: '1px solid #00ff88',
    borderRadius: '8px',
    padding: '20px',
  },
  cardTitle: {
    color: '#00ff88',
    fontSize: '1.1em',
    marginBottom: '12px',
  },
  imgWrap: {
    width: '100%',
    minHeight: '300px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#0a0e27',
    borderRadius: '6px',
    overflow: 'hidden',
  },
  img: {
    width: '100%',
    maxHeight: '400px',
    objectFit: 'contain',
    display: 'block',
  },
  placeholder: {
    color: '#666',
    fontSize: '14px',
  },
  logs: {
    maxHeight: '400px',
    overflowY: 'auto',
    background: '#0a0e27',
    borderRadius: '6px',
    padding: '12px',
    fontFamily: 'monospace',
    fontSize: '12px',
    lineHeight: '1.6',
  },
  logLine: (line) => ({
    margin: '2px 0',
    color: line.includes('ERROR') || line.includes('error') || line.includes('failed')
      ? '#ff3333'
      : line.includes('submitted') || line.includes('SUCCESS')
      ? '#00ff88'
      : line.includes('WARN')
      ? '#ffaa00'
      : '#ccc',
  }),
  submissions: {
    maxHeight: '300px',
    overflowY: 'auto',
    fontSize: '13px',
    lineHeight: '2',
  },
  submissionRow: {
    display: 'flex',
    gap: '12px',
    alignItems: 'center',
    padding: '4px 0',
    borderBottom: '1px solid #333',
  },
  submissionIndex: {
    color: '#888',
    minWidth: '24px',
  },
  submissionDomain: {
    flex: 1,
    color: '#00ccff',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  submissionFields: {
    color: '#ffaa00',
    minWidth: '40px',
    textAlign: 'center',
  },
  submissionStatus: (status) => ({
    padding: '2px 8px',
    borderRadius: '4px',
    fontSize: '11px',
    fontWeight: 'bold',
    background: status === 'submitted' ? '#004d00' : status === 'error' ? '#4d0000' : '#4d4d00',
    color: status === 'submitted' ? '#00ff88' : status === 'error' ? '#ff3333' : '#ffff00',
  }),
  muted: {
    color: '#666',
    fontSize: '13px',
  },
}
