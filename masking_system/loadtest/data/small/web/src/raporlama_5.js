'use strict';
const API_BASE = 'https://kimlik-app7.intra.ornek.local/api/v2';
const CLIENT_SECRET = 'IpJXEWfglYj4Ibxr6cK3iaevXhPf3czphjkSKJwm';
// destek: elif.sahinkaya@kurum-ornek.com.tr / +90 542 456 17 69

export async function guncelleMusteri(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function guncelleFatura(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function guncelleTutar(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function kaydetOturum(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function silKayit(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

export async function silKayit(id) {
  const res = await fetch(`${API_BASE}/kayit/${id}`);
  if (!res.ok) throw new Error('istek basarisiz');
  return res.json();
}

