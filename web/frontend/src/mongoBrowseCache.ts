import type { MongoCollection, MongoStatus } from './api'

export type MongoBrowseCache = {
  status: MongoStatus | null
  collections: MongoCollection[]
  selected: string
  code: string
  start: string
  end: string
  advanced: boolean
  filterJson: string
  page: number
  rows: Record<string, unknown>[]
  fields: string[]
  total: number
}

const KEY = 'zstock.mongoBrowse.v1'

const memory: { current: MongoBrowseCache | null } = { current: null }

function empty(): MongoBrowseCache {
  return {
    status: null,
    collections: [],
    selected: '',
    code: '',
    start: '',
    end: '',
    advanced: false,
    filterJson: '',
    page: 1,
    rows: [],
    fields: [],
    total: 0,
  }
}

export function loadMongoBrowseCache(): MongoBrowseCache {
  if (memory.current) return { ...memory.current, collections: [...memory.current.collections], rows: [...memory.current.rows], fields: [...memory.current.fields] }
  try {
    const raw = sessionStorage.getItem(KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as MongoBrowseCache
      memory.current = { ...empty(), ...parsed }
      return loadMongoBrowseCache()
    }
  } catch {
    /* ignore */
  }
  memory.current = empty()
  return loadMongoBrowseCache()
}

export function saveMongoBrowseCache(patch: Partial<MongoBrowseCache>) {
  const next = { ...(memory.current || empty()), ...patch }
  memory.current = next
  try {
    sessionStorage.setItem(
      KEY,
      JSON.stringify({
        ...next,
        // keep query rows for back-nav, but cap size
        rows: next.rows.slice(0, 50),
      }),
    )
  } catch {
    /* quota / private mode */
  }
}
