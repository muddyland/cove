/**
 * WebAuthn glue: Cove sends options as JSON with base64url binary fields; the
 * browser API wants ArrayBuffers, and answers with ArrayBuffers Cove wants back
 * as base64url. Done by hand rather than with PublicKeyCredential.toJSON() /
 * parse*OptionsFromJSON, which older browsers lack.
 */

export function b64uToBuf(s: string): ArrayBuffer {
  const pad = '='.repeat((4 - (s.length % 4)) % 4)
  const bin = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out.buffer
}

export function bufToB64u(buf: ArrayBuffer | null | undefined): string | null {
  if (!buf) return null
  const bytes = new Uint8Array(buf)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

const IP_HOST = /^(\d{1,3}(\.\d{1,3}){3}|\[?[0-9a-f:]+\]?)$/i

/** Passkeys work here: the browser supports WebAuthn and Cove is opened by a
 *  domain name (WebAuthn refuses IP addresses). */
export function passkeysAvailable(): boolean {
  return typeof window !== 'undefined' && !!window.PublicKeyCredential && !IP_HOST.test(window.location.hostname)
}

/** A user cancelling (or the prompt timing out) isn't an error worth a red box. */
/** The key already holds a passkey for this account (the browser refuses a duplicate). */
export function isAlreadyRegistered(e: unknown): boolean {
  return e instanceof DOMException && e.name === 'InvalidStateError'
}

export function isCancelled(e: unknown): boolean {
  return e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'AbortError')
}

export async function createCredential(options: Record<string, any>): Promise<Record<string, unknown>> {
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...options,
    challenge: b64uToBuf(options.challenge),
    user: { ...options.user, id: b64uToBuf(options.user.id) },
    excludeCredentials: (options.excludeCredentials ?? []).map((c: any) => ({ ...c, id: b64uToBuf(c.id) })),
  } as PublicKeyCredentialCreationOptions
  const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential
  const resp = cred.response as AuthenticatorAttestationResponse
  return {
    id: cred.id,
    rawId: bufToB64u(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufToB64u(resp.clientDataJSON),
      attestationObject: bufToB64u(resp.attestationObject),
      transports: typeof resp.getTransports === 'function' ? resp.getTransports() : [],
    },
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
  }
}

export async function getAssertion(options: Record<string, any>): Promise<Record<string, unknown>> {
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...options,
    challenge: b64uToBuf(options.challenge),
    allowCredentials: (options.allowCredentials ?? []).map((c: any) => ({ ...c, id: b64uToBuf(c.id) })),
  } as PublicKeyCredentialRequestOptions
  const cred = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential
  const resp = cred.response as AuthenticatorAssertionResponse
  return {
    id: cred.id,
    rawId: bufToB64u(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufToB64u(resp.clientDataJSON),
      authenticatorData: bufToB64u(resp.authenticatorData),
      signature: bufToB64u(resp.signature),
      userHandle: bufToB64u(resp.userHandle),
    },
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
  }
}
