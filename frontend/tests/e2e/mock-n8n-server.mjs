import crypto from 'node:crypto'
import http from 'node:http'

const port = Number(process.env.MOCK_N8N_PORT ?? 5678)
const secret = process.env.N8N_WEBHOOK_HMAC_SECRET ?? 'e2e-n8n-secret'
const state = {
  hold: false,
  dispatches: 0,
  callbacks: 0,
  heldKeys: new Set(),
  heldResponses: [],
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = []
    request.on('data', (chunk) => chunks.push(chunk))
    request.on('end', () => resolve(Buffer.concat(chunks)))
    request.on('error', reject)
  })
}

function signature(body, timestamp) {
  return (
    'sha256=' +
    crypto.createHmac('sha256', secret).update(`${timestamp}.`).update(body).digest('hex')
  )
}

function send(response, status, body, contentType = 'application/json') {
  const payload = typeof body === 'string' ? body : JSON.stringify(body)
  response.writeHead(status, {
    'Content-Type': contentType,
    'Content-Length': Buffer.byteLength(payload),
  })
  response.end(payload)
}

function verify(request, rawBody) {
  const timestamp = request.headers['x-yasmina-timestamp']
  const provided = request.headers['x-yasmina-signature']
  if (typeof timestamp !== 'string' || typeof provided !== 'string') {
    return false
  }
  const expected = signature(rawBody, timestamp)
  const expectedBuffer = Buffer.from(expected)
  const providedBuffer = Buffer.from(provided)
  return (
    expectedBuffer.length === providedBuffer.length &&
    crypto.timingSafeEqual(expectedBuffer, providedBuffer)
  )
}

function buildQuote(envelope) {
  const supplier = envelope.suppliers?.[0]
  const items = envelope.tender?.items ?? []
  const total = items.reduce((sum, item) => sum + Number(item.quantity) * 10, 0)
  return {
    supplier_name: supplier?.name ?? 'n8n supplier',
    supplier_contact_email: supplier?.email ?? null,
    quote_reference: `N8N-${String(envelope.event_id).slice(0, 8)}`,
    tender_id: envelope.tender.tender_id,
    currency: envelope.tender.currency,
    valid_until: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
    payment_terms: 'Net 30',
    incoterms: 'DAP',
    delivery_days: 2,
    total_net: total.toFixed(2),
    raw_extraction: { source: 'n8n' },
    lines: items.map((item, index) => ({
      line_number: index + 1,
      description: item.description,
      tender_item_id: item.tender_item_id,
      quantity: item.quantity,
      unit: item.unit,
      unit_price_net: '10.0000',
      delivery_days: 2,
    })),
  }
}

async function sendCallback(envelope) {
  const body = JSON.stringify({
    event_id: envelope.event_id,
    quotes: [buildQuote(envelope)],
  })
  const timestamp = Math.floor(Date.now() / 1000)
  const response = await fetch(envelope.callback_url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': `n8n-quote-${envelope.event_id}`,
      'X-Yasmina-Timestamp': String(timestamp),
      'X-Yasmina-Signature': signature(body, timestamp),
    },
    body,
  })
  const text = await response.text()
  if (!response.ok) {
    throw new Error(`callback returned ${response.status}: ${text}`)
  }
  state.callbacks += 1
  return response.status
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${port}`)

  if (url.pathname === '/health' || url.pathname === '/healthz') {
    send(response, 200, { status: 'ok' })
    return
  }

  if (url.pathname === '/control/hold' && request.method === 'POST') {
    state.hold = true
    state.heldKeys.clear()
    send(response, 200, { hold: true })
    return
  }

  if (url.pathname === '/control/release' && request.method === 'POST') {
    state.hold = false
    for (const held of state.heldResponses.splice(0)) {
      send(held, 200, { accepted: true, released: true })
    }
    state.heldKeys.clear()
    send(response, 200, { hold: false })
    return
  }

  if (url.pathname === '/control/state') {
    send(response, 200, {
      hold: state.hold,
      dispatches: state.dispatches,
      callbacks: state.callbacks,
      held: state.heldKeys.size,
    })
    return
  }

  if (url.pathname !== '/webhook/yasmina/rfq' || request.method !== 'POST') {
    send(response, 404, { detail: 'not found' })
    return
  }

  const rawBody = await readBody(request)
  if (!verify(request, rawBody)) {
    send(response, 401, { detail: 'invalid HMAC signature' })
    return
  }

  let envelope
  try {
    envelope = JSON.parse(rawBody.toString('utf8'))
  } catch {
    send(response, 400, { detail: 'invalid JSON' })
    return
  }
  if (
    envelope.event_type !== 'rfq.dispatch' ||
    typeof envelope.callback_url !== 'string' ||
    !Array.isArray(envelope.tender?.items) ||
    envelope.tender.items.length === 0 ||
    !Array.isArray(envelope.suppliers) ||
    envelope.suppliers.length === 0
  ) {
    send(response, 422, { detail: 'invalid RFQ envelope' })
    return
  }

  state.dispatches += 1
  const key = String(request.headers['idempotency-key'] ?? envelope.idempotency_key)
  if (state.hold && !state.heldKeys.has(key)) {
    state.heldKeys.add(key)
    state.heldResponses.push(response)
    console.log(`[mock-n8n] holding dispatch ${key}`)
    return
  }

  try {
    const callbackStatus = await sendCallback(envelope)
    send(response, 200, {
      accepted: true,
      quote_reference: `N8N-${String(envelope.event_id).slice(0, 8)}`,
      callback_status: callbackStatus,
    })
    console.log(`[mock-n8n] dispatched ${envelope.event_id} and delivered callback`)
  } catch (error) {
    send(response, 502, { detail: String(error) })
  }
})

server.listen(port, '127.0.0.1', () => {
  console.log(`[mock-n8n] listening on http://127.0.0.1:${port}`)
})

function shutdown() {
  for (const held of state.heldResponses.splice(0)) {
    held.destroy()
  }
  server.close(() => process.exit(0))
}

process.on('SIGTERM', shutdown)
process.on('SIGINT', shutdown)
