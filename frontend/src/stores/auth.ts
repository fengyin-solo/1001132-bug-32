import { defineStore } from 'pinia'

const TOKEN_KEY = 'slope-handover-token'
const TEAM_KEY = 'slope-handover-team'
const NAME_KEY = 'slope-handover-name'

interface Team {
  id: number
  name: string
  code: string
}

interface LoginEntry {
  token: string
  team_code: string
  team_name: string
  inspector: string
}

export const useAuthStore = defineStore('handover-auth', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) ?? '',
    teamCode: localStorage.getItem(TEAM_KEY) ?? '',
    teamName: localStorage.getItem(NAME_KEY) ?? '',
    teams: [] as Team[],
    message: '',
  }),
  getters: {
    loggedIn: (state) => state.token.length > 0,
  },
  actions: {
    async loadTeams() {
      const res = await fetch('/api/slope-handover/teams')
      const data = await res.json()
      this.teams = data.items ?? []
    },
    async login(code: string, inspector?: string) {
      const res = await fetch('/api/slope-handover/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ team_code: code, inspector }),
      })
      const data = await res.json()
      if (!res.ok || !data.ok) {
        throw new Error(data.detail ?? data.message ?? '登录失败')
      }
      const entry = data.entry as LoginEntry
      this.setSession(entry.token, entry.team_code, entry.team_name)
    },
    logout() {
      this.token = ''
      this.teamCode = ''
      this.teamName = ''
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(TEAM_KEY)
      localStorage.removeItem(NAME_KEY)
    },
    setSession(token: string, code: string, name: string) {
      this.token = token
      this.teamCode = code
      this.teamName = name
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.setItem(TEAM_KEY, code)
      localStorage.setItem(NAME_KEY, name)
    },
  },
})
