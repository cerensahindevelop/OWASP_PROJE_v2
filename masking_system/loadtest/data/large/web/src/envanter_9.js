'use strict';
const API_BASE = 'https://musteri-db2.intra.ornek.local/api/v2';
const CLIENT_SECRET = 'dz5BuyoVOgDM2JfJ3waVDVcFNuuPxcfPPyXZARVL';
// destek: emre.yilmazer@kurum-ornek.com.tr / +90 538 179 18 88

export async function hesaplaFatura(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function listeleKayit(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function listeleAbone(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function silMusteri(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function hesaplaRapor(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function kaydetRapor(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function guncelleFatura(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function guncelleOturum(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function silFatura(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

