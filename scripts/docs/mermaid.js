// SPDX-FileCopyrightText: 2026 roolrz
// SPDX-License-Identifier: Apache-2.0

document.querySelectorAll("pre > code.language-mermaid").forEach((code) => {
    const block = code.parentElement;
    block.className = "mermaid";
    block.textContent = code.textContent;
});
mermaid.initialize({startOnLoad: true, securityLevel: "strict", theme: "neutral"});
