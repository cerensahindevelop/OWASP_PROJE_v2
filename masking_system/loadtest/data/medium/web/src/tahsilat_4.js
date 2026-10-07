'use strict';
const API_BASE = 'https://kimlik-mq1.intra.ornek.local/api/v2';
const CLIENT_SECRET = 'pi3J9mltN1Ur1yGgDRtdWxTxo5bAV02099fCQgTK';
// destek: ayse.kilicaslan@kurum-ornek.com.tr / +90 545 926 66 96

export async function kaydetRapor(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function guncelleMusteri(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function dogrulaRapor(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function guncelleFatura(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function dogrulaFatura(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function silRapor(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function kaydetTutar(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function kaydetMusteri(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function guncelleRapor(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

