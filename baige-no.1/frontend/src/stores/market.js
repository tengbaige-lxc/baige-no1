import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getBalance } from '@/api/market'
import { getLivePositions } from '@/api/position'

export const useMarketStore = defineStore('market', () => {
  const symbol = ref('SOL-USDT')
  const ticker = ref({ price: 0, open24h: 0, high24h: 0, low24h: 0, vol24h: 0 })
  const orderbook = ref({ bidPrice: 0, bidQty: 0, askPrice: 0, askQty: 0, bids: [], asks: [] })
  const balance = ref(0)
  const positions = ref([])
  const totalPnl = ref(0)
  const positionCount = ref(0)
  const connected = ref(false)

  let evtSource = null
  let privateTimer = null
  let visibilityHandler = null
  let isPaused = false

  const currentPrice = computed(() => ticker.value.price || 0)
  const priceChange = computed(() => {
    const open = ticker.value.open24h
    if (!open || open === 0) return 0
    return ((currentPrice.value - open) / open) * 100
  })

  function init(sym) {
    symbol.value = sym
    close()

    // SSE for public market data
    const url = `/api/v1/stream?symbol=${sym}`
    evtSource = new EventSource(url)
    connected.value = true

    evtSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'market') {
          ticker.value = data.ticker || ticker.value
          orderbook.value = data.orderbook || orderbook.value
        }
      } catch (e) {
        console.error('SSE parse error', e)
      }
    }

    evtSource.onerror = () => {
      connected.value = false
    }

    // Poll private data
    fetchPrivate()
    privateTimer = setInterval(() => {
      if (!isPaused) fetchPrivate()
    }, 3000)

    // Page visibility handling
    visibilityHandler = () => {
      isPaused = document.hidden
      if (document.hidden && evtSource) {
        evtSource.close()
        connected.value = false
      } else if (!document.hidden && !connected.value) {
        init(symbol.value)
      }
    }
    document.addEventListener('visibilitychange', visibilityHandler)
  }

  async function fetchPrivate() {
    try {
      const [balRes, posRes] = await Promise.allSettled([
        getBalance(),
        getLivePositions()
      ])

      if (balRes.status === 'fulfilled') {
        const usdt = balRes.value.data?.find?.(b => b.asset === 'USDT')
        balance.value = usdt ? usdt.free : 0
      }

      if (posRes.status === 'fulfilled') {
        const res = posRes.value
        if (res.code === 200) {
          positions.value = res.data.positions || []
          totalPnl.value = res.data.total_pnl || 0
          positionCount.value = res.data.position_count || 0
        }
      }
    } catch (e) {
      // ignore
    }
  }

  function close() {
    if (evtSource) {
      evtSource.close()
      evtSource = null
    }
    if (privateTimer) {
      clearInterval(privateTimer)
      privateTimer = null
    }
    if (visibilityHandler) {
      document.removeEventListener('visibilitychange', visibilityHandler)
      visibilityHandler = null
    }
    connected.value = false
  }

  return {
    symbol,
    ticker,
    orderbook,
    balance,
    positions,
    totalPnl,
    positionCount,
    connected,
    currentPrice,
    priceChange,
    init,
    close,
    fetchPrivate,
  }
})
