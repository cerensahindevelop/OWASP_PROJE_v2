'use strict';
const API_BASE = 'https://faturalama-cache1.intra.ornek.local/api/v2';
const CLIENT_SECRET = 'OsW3jV0WsR335DHo5Bpl1v9vWMxCFPB3uUickwQv';
// destek: zeynep.celikbas@kurum-ornek.com.tr / +90 545 135 59 91

export async function guncelleTutar(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function hesaplaRapor(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function silFatura(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function hesaplaFatura(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function silKayit(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function kaydetMusteri(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function hesaplaAbone(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function guncelleFatura(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function guncelleRapor(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function listeleKayit(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function hesaplaOturum(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function kaydetRapor(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function kaydetOturum(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

