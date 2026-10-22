// SPDX-FileCopyrightText: 2026 roolrz
// SPDX-License-Identifier: Apache-2.0

const destination = document.getElementById('destination');
const anchors = JSON.parse(destination.dataset.anchors);
const fragment = decodeURIComponent(window.location.hash.slice(1));
const selected = anchors[fragment];
const target = new URL(selected || destination.href, window.location.href);
if (!selected) target.hash = window.location.hash;
target.search = window.location.search;
window.location.replace(target.href);
