import { useEffect, useState } from 'react'
import axios from 'axios'

const API = process.env.NEXT_PUBLIC_API_URL

export default function Dashboard() {
  const [data, setData] = useState({
    status: 'Monitoring...',
    latest_screenshot: null,
    logs: [],
    submitted: [],
    submitted_count: 0,
    processed_count: 0,
  })

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await axios.get(`${API}/dashboard/api/status`)
        setData(res.data)
      } catch (e) {
        console.error('API error:', e)
      }
    }, 1000)

    return () => clearInterval(interval)
  }, [])

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h1>🚀 Orbital Real-Time</h1>
        <div style={styles.stats}>
          <div>Status: <strong>{data.status}</strong></div>
          <div>Submitted: <strong>{data.submitted_count}</strong></div>
          <div>Processed: <strong>{data.processed_count}</strong></div>
        </div>
      </div>

      <div style={styles.grid}>
        <div style={styles.card}>
          <h2>📸 Latest</h2>
          {data.latest_screenshot ? (
            <img 
              src={`${API}/static/screenshots/${data.latest_screenshot}`}
              alt="Latest"
              style={styles.img}
            />
          ) : (
            <p>Waiting for screenshots...</p>
          )}
        </div>

        <div style={styles.card}>
          <h2>📝 Logs</h2>
          <div style={styles.logs}>
            {data.logs.slice(-30).map((line, i) => (
              <div key={i} style={styles.logLine}>{line}</div>
            ))}
          </div>
        </div>
      </div>

      <div style={styles.card}>
        <h2>✅ Submitted Forms</h2>
        {data.submitted.map((s, i) => (
          <div key={i}>{i + 1}. {s.domain} - {s.fields_filled}/{s.fields_total}</div>
        ))}
      </div>
    </div>
  )
}

const styles = {
  container: { padding: '20px', fontFamily: 'monospace', background: '#0a0e27', color: '#e0e0e0', minHeight: '100vh' },
  header: { marginBottom: '30px' },
  stats: { display: 'flex', gap: '20px', marginTop: '10px', fontSize: '14px' },
  grid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '20px' },
  card: { background: '#1a1e3f', border: '1px solid #00ff88', padding: '20px', borderRadius: '8px' },
  img: { width: '100%', maxHeight: '400px', objectFit: 'contain' },
  logs: { maxHeight: '400px', overflowY: 'auto', fontSize: '12px' },
  logLine: { margin: '4px 0' },
}
