# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Normalize a missing single-letter index anchor in Doxygen 1.18 output."""

import re


def fix_index_anchor(text):
    # With one letter and a short list, globals_func.html contains a jump bar
    # but no section heading. Bind the jump to that list only when every entry
    # actually has that initial. Other missing anchors remain validation errors.
    pattern = (r'(<div class="qindex"><a class="qindex" href="#index_([a-z])">'
               r'[A-Z]</a></div>\s*)<ul>(.*?)</ul>')

    def repair(match):
        prefix, letter, entries = match.groups()
        if f'id="index_{letter}"' in text or f'name="index_{letter}"' in text:
            return match.group(0)
        names = re.findall(r'<li>([A-Za-z_]\w*)\(', entries)
        if not names or len(names) != entries.count('<li>') or any(
                not name.lower().startswith(letter) for name in names):
            return match.group(0)
        return f'{prefix}<ul id="index_{letter}">{entries}</ul>'

    return re.sub(pattern, repair, text, flags=re.DOTALL)
