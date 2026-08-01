import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { MetaProvider } from './meta'
import { Sessions } from './pages/Sessions'
import { SessionReview } from './pages/SessionReview'
import { PerClub } from './pages/PerClub'
import { StrikeVsOutcome } from './pages/StrikeVsOutcome'
import { Trend } from './pages/Trend'
import { Dispersion } from './pages/Dispersion'
import { ShotBrowser } from './pages/ShotBrowser'
import { Insight } from './pages/Insight'

const NAV = [
  { to: '/sessions', label: 'Sessions' },
  { to: '/strike', label: 'Strike vs outcome' },
  { to: '/clubs', label: 'Per club' },
  { to: '/trend', label: 'Session over session' },
  { to: '/dispersion', label: 'Dispersion' },
  { to: '/shots', label: 'Shot browser' },
  { to: '/ask', label: 'Ask' },
]

export function App() {
  return (
    <MetaProvider>
      <div className="app">
        <header className="topbar">
          <span className="brand">Golf Session Analyzer</span>
          <nav className="nav">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => (isActive ? 'active' : '')}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/strike" replace />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/:id" element={<SessionReview />} />
            <Route path="/clubs" element={<PerClub />} />
            <Route path="/strike" element={<StrikeVsOutcome />} />
            <Route path="/trend" element={<Trend />} />
            <Route path="/dispersion" element={<Dispersion />} />
            <Route path="/shots" element={<ShotBrowser />} />
            <Route path="/ask" element={<Insight />} />
          </Routes>
        </main>
      </div>
    </MetaProvider>
  )
}
