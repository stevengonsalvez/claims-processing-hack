import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import Claimant from './Claimant.tsx'

// Two rooms, one bundle: /claim/<id> is the claimant's letter and appeal, everything
// else is the bench. Client-side on pathname — a router would be a dependency for one branch.
const m = /^\/claim\/([^/?#]+)/.exec(location.pathname)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {m ? <Claimant id={decodeURIComponent(m[1])} /> : <App />}
  </StrictMode>,
)
