/**
 * Converts plain text to styled HTML for flashcard answers.
 *
 * Detects structure patterns:
 * - Lines ending with ":" alone = section headers (bold)
 * - Lines with ":" followed by content = definition paragraphs
 * - Lines following a header = bullet list items
 * - Explicit bullets (•, -, *) = list items
 */
export function formatCardContent(text: string): string {
  if (!text) return '';

  // Extract source citation if present (at end of text)
  const sourceMatch = text.match(/\s*Sources?:\s*(.+)$/i);
  let sourceText = '';
  if (sourceMatch) {
    sourceText = sourceMatch[1];
    text = text.replace(/\s*Sources?:\s*.+$/i, '');
  }

  const lines = text.split('\n');
  const result: string[] = [];
  let inList = false;
  let afterHeader = false;

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (!trimmed) {
      // Empty line closes list
      if (inList) {
        result.push('</ul>');
        inList = false;
      }
      afterHeader = false;
      continue;
    }

    // Check for explicit bullet patterns
    const bulletMatch = trimmed.match(/^[•\-*]\s+(.+)$/);
    if (bulletMatch) {
      if (!inList) {
        result.push('<ul class="pl-5 space-y-1 my-2">');
        inList = true;
      }
      result.push(`<li class="flex gap-2"><span class="text-gray-400">•</span><span>${bulletMatch[1]}</span></li>`);
      continue;
    }

    // Check for section header (word(s) ending with colon only, e.g., "Characteristics:")
    const isHeader = /^[A-Z][^:]*:$/.test(trimmed) && trimmed.length < 50;

    // Check for definition line (e.g., "Interview tip: some content")
    const definitionMatch = trimmed.match(/^([A-Z][^:]+):\s+(.+)$/);

    if (isHeader) {
      // Close any open list
      if (inList) {
        result.push('</ul>');
        inList = false;
      }
      result.push(`<p class="font-semibold text-gray-900 dark:text-white mt-4 mb-1">${trimmed}</p>`);
      afterHeader = true;
    } else if (definitionMatch) {
      // Close any open list
      if (inList) {
        result.push('</ul>');
        inList = false;
      }
      afterHeader = false;
      // Style the label part differently
      result.push(`<p class="my-2"><span class="font-medium">${definitionMatch[1]}:</span> ${definitionMatch[2]}</p>`);
    } else if (afterHeader) {
      // Lines after a header become list items with explicit bullet
      if (!inList) {
        result.push('<ul class="pl-5 space-y-1 my-1">');
        inList = true;
      }
      result.push(`<li class="flex gap-2"><span class="text-gray-400">•</span><span>${trimmed}</span></li>`);
    } else {
      // Regular paragraph
      if (inList) {
        result.push('</ul>');
        inList = false;
      }
      result.push(`<p class="my-2">${trimmed}</p>`);
    }
  }

  // Close any open list
  if (inList) {
    result.push('</ul>');
  }

  // Append source citation with distinct styling
  if (sourceText) {
    result.push(`<p class="mt-3 pt-2 border-t border-slate-600/30 text-xs text-slate-400 italic">Source: ${sourceText}</p>`);
  }

  return result.join('');
}
