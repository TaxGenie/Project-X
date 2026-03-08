/**
 * TEJAS — Professional Word Report Generator
 * Updated for new KEY SUMMARY single-section output from llm_engine.py
 * Light-background, print-ready.
 */
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, BorderStyle, WidthType, ShadingType, PageBreak,
  Header, Footer, PageNumber, TabStopType, TabStopPosition
} = require('docx');
const fs   = require('fs');
const os   = require('os');
const path = require('path');

const TEMP_JSON = path.join(os.tmpdir(), 'comparison_data.json');
const TEMP_DOCX = path.join(os.tmpdir(), 'comparison_output.docx');

const raw    = JSON.parse(fs.readFileSync(TEMP_JSON, 'utf8'));
const query  = raw.query         || '';
const result = raw.result        || '';
const genOn  = raw.generated_on  || '';

// ─── Parse result — new engine puts everything in sec3 ───────────────────────
// Supports both old (4-section) and new (KEY SUMMARY only) formats
function parseSection(text, marker) {
  var re = new RegExp(marker + '[\\s\\S]*?===([\\s\\S]*?)(?====|$)');
  var m  = text.match(re);
  return m ? m[1].trim() : '';
}

var sec3 = parseSection(result, 'SECTION 3');
// Fallback: if marker not found, treat entire result as the summary
if (!sec3 && result.trim()) {
  sec3 = result.trim();
}

// Sub-sections within the KEY SUMMARY — each becomes a Word section
var SUB_SECTIONS = [
  { key: 'What this provision does',        accent: '1565C0', fill: 'E8EBF5' },
  { key: 'Who it applies to',               accent: '2E7D32', fill: 'E8F5E9' },
  { key: 'The rules explained simply',      accent: '6A1B9A', fill: 'F3E5F5' },
  { key: 'Key thresholds and rates',        accent: 'B45309', fill: 'FFF8E1' },
  { key: 'What happens if you don\'t comply', accent: 'C62828', fill: 'FFEBEE' },
  { key: 'Worked example',                  accent: '00695C', fill: 'E0F2F1' },
  { key: 'Note on 2025 Act structure',      accent: '37474F', fill: 'ECEFF1' },
];

/**
 * Split sec3 into named sub-sections using the bold heading markers.
 * Returns { sectionName: text, ... }
 */
function splitSubSections(text) {
  var result = {};
  var lines  = text.split('\n');
  var current = '__preamble__';
  var buffer  = [];

  lines.forEach(function(line) {
    // Matches **Heading text** on its own line (with or without trailing **)
    var m = line.trim().match(/^\*\*(.+?)\*\*\s*$/);
    if (m) {
      if (buffer.length) result[current] = buffer.join('\n').trim();
      current = m[1].trim();
      buffer  = [];
    } else {
      buffer.push(line);
    }
  });
  if (buffer.length) result[current] = buffer.join('\n').trim();
  return result;
}

var subSections = splitSubSections(sec3);

// ─── Colour palette ───────────────────────────────────────────────────────────
var C = {
  NAVY        : '1A237E',
  NAVY_MED    : '283593',
  GOLD        : '8B6914',
  COVER_NAVY  : '1A237E',
  TH_FILL     : '1A237E',
  TR_ODD      : 'F5F7FF',
  TR_EVEN     : 'FFFFFF',
  BLACK       : '1A1A1A',
  DARK        : '212121',
  BODY        : '37474F',
  MUTED       : '546E7A',
  WHITE       : 'FFFFFF',
  LIGHT_RULE  : 'C5CAE9',
};

// Page / margin
var PAGE_W = 12240;
var PAGE_H = 15840;
var MARGIN = 1296;
var CW     = PAGE_W - 2 * MARGIN;

// ─── Border helpers ───────────────────────────────────────────────────────────
function bNone()  { return { style: BorderStyle.NONE,   size: 0,  color: 'FFFFFF' }; }
function bThin(c) { return { style: BorderStyle.SINGLE, size: 2,  color: c || C.LIGHT_RULE }; }
function bMed(c)  { return { style: BorderStyle.SINGLE, size: 6,  color: c || C.NAVY }; }
var NO_BORDER = { top:bNone(), bottom:bNone(), left:bNone(), right:bNone() };
function allThin(c){ return { top:bThin(c), bottom:bThin(c), left:bThin(c), right:bThin(c) }; }

function sp(b,a){ return { before: b||0, after: a||0 }; }
function ep(a)  { return new Paragraph({ spacing: sp(0, a||120) }); }

function R(text, opts) {
  return new TextRun(Object.assign({ text: String(text||''), font:'Calibri' }, opts||{}));
}

// ─── Cover page ───────────────────────────────────────────────────────────────
function buildCover() {
  return [
    new Table({
      width: { size: CW, type: WidthType.DXA },
      columnWidths: [CW],
      rows: [new TableRow({ children: [new TableCell({
        borders: NO_BORDER,
        width: { size: CW, type: WidthType.DXA },
        shading: { fill: C.COVER_NAVY, type: ShadingType.CLEAR },
        margins: { top: 800, bottom: 800, left: 600, right: 600 },
        children: [
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: sp(0, 80),
            children: [ R('⚖', { size:52, color:'C9A84C' }) ],
          }),
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: sp(0, 60),
            children: [ R('Tax Cookies', { font:'Georgia', size:88, bold:true, color:'C9A84C' }) ],
          }),
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: sp(0, 200),
            children: [ R('Where Every Tax Query Finds an Answer', { size:22, italics:true, color:'B0BEC5' }) ],
          }),
          new Paragraph({
            border: { bottom: { style:BorderStyle.SINGLE, size:4, color:'8B6914' } },
            spacing: sp(0, 200),
          }),
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: sp(0, 120),
            children: [ R('INCOME TAX KEY SUMMARY REPORT', { size:18, bold:true, color:'90A4AE', characterSpacing:160 }) ],
          }),
          new Paragraph({
            spacing: sp(0, 40),
            children: [ R('SECTION / TOPIC', { size:16, bold:true, color:'78909C', characterSpacing:200 }) ],
          }),
          new Paragraph({
            spacing: sp(0, 200),
            children: [ R(query, { font:'Georgia', size:30, bold:true, color:'FFFFFF' }) ],
          }),
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: sp(0, 40),
            children: [
              R('Generated on  ', { size:18, color:'90A4AE' }),
              R(genOn, { size:18, bold:true, color:'C9A84C' }),
            ],
          }),
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: sp(0, 0),
            children: [ R('Based on Actual 2025 Act Text', { size:16, italics:true, color:'546E7A' }) ],
          }),
        ],
      })]})],
    }),
    new Paragraph({ children: [new PageBreak()], spacing: sp(0,0) }),
  ];
}

// ─── Sub-section banner ───────────────────────────────────────────────────────
function subSectionHeader(title, fillColor, accentColor) {
  return [
    ep(200),
    new Table({
      width: { size: CW, type: WidthType.DXA },
      columnWidths: [CW],
      rows: [new TableRow({ children: [new TableCell({
        borders: {
          top:    bMed(accentColor),
          bottom: bNone(),
          left:   bMed(accentColor),
          right:  bNone(),
        },
        width: { size: CW, type: WidthType.DXA },
        shading: { fill: fillColor, type: ShadingType.CLEAR },
        margins: { top: 140, bottom: 140, left: 280, right: 280 },
        children: [new Paragraph({
          keepWithNext: true,
          spacing: sp(0, 0),
          children: [
            R(title, { font:'Georgia', size:24, bold:true, color:C.NAVY }),
          ],
        })],
      })]})],
    }),
    new Paragraph({
      keepWithNext: true,
      border: { bottom: { style:BorderStyle.SINGLE, size:4, color:accentColor } },
      spacing: sp(0, 160),
    }),
  ];
}

// ─── Prose renderer ───────────────────────────────────────────────────────────
function buildProse(text, accentColor) {
  var els   = [];
  var lines = text.split('\n');

  lines.forEach(function(line) {
    var t = line.trim();
    if (!t) { els.push(ep(60)); return; }

    // ## sub-heading
    if (/^#{2,3}\s/.test(t)) {
      els.push(new Paragraph({
        keepWithNext: true,
        spacing: sp(220, 80),
        border: { bottom: { style:BorderStyle.SINGLE, size:2, color:accentColor } },
        children: [ R(t.replace(/^#{2,3}\s*/,'').replace(/\*\*/g,''), { font:'Georgia', size:22, bold:true, color:accentColor }) ],
      }));
      return;
    }

    // **Bold heading** on its own line
    if (/^\*\*[^*]+\*\*\s*$/.test(t)) {
      els.push(new Paragraph({
        keepWithNext: true,
        spacing: sp(200, 60),
        children: [ R(t.replace(/\*\*/g,''), { size:21, bold:true, color:accentColor }) ],
      }));
      return;
    }

    // **Label**: value
    var kv = t.match(/^\*\*(.+?)\*\*[:\s—–-]+(.+)/);
    if (kv) {
      els.push(new Paragraph({
        keepWithNext: true,
        spacing: sp(160, 60),
        children: [
          R(kv[1] + ':  ', { size:20, bold:true, color:accentColor }),
          R(kv[2].replace(/\*\*/g,''), { size:20, color:C.BODY }),
        ],
      }));
      return;
    }

    // Bullet
    if (/^[*\-]\s/.test(t)) {
      var bText = t.replace(/^[*\-]\s/,'').replace(/\*\*/g,'');
      var parts = bText.split(/(\*\*[^*]+\*\*)/);
      var runs  = parts.map(function(p) {
        if (/^\*\*/.test(p)) return R(p.replace(/\*\*/g,''), { size:19, bold:true, color:C.DARK });
        return R(p, { size:19, color:C.BODY });
      });
      els.push(new Paragraph({
        spacing: sp(40, 60),
        indent: { left: 480, hanging: 240 },
        children: [R('—  ', { size:19, bold:true, color:accentColor })].concat(runs),
      }));
      return;
    }

    // Numbered list
    var numbered = t.match(/^(\d+)\.\s+(.*)/);
    if (numbered) {
      els.push(new Paragraph({
        spacing: sp(40, 60),
        indent: { left: 560, hanging: 360 },
        children: [
          R(numbered[1] + '.  ', { size:19, bold:true, color:accentColor }),
          R(numbered[2].replace(/\*\*/g,''), { size:19, color:C.BODY }),
        ],
      }));
      return;
    }

    // Normal paragraph — handle inline **bold**
    var parts2 = t.split(/(\*\*[^*]+\*\*)/);
    var pruns  = parts2.map(function(p) {
      if (/^\*\*/.test(p)) return R(p.replace(/\*\*/g,''), { size:20, bold:true, color:C.DARK });
      return R(p, { size:20, color:C.BODY });
    });
    els.push(new Paragraph({ spacing: sp(40, 80), children: pruns }));
  });

  return els;
}

// ─── Header & Footer ──────────────────────────────────────────────────────────
var docHeader = new Header({
  children: [new Paragraph({
    border: { bottom: { style:BorderStyle.SINGLE, size:4, color:C.NAVY } },
    spacing: sp(0, 80),
    tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
    children: [
      R('Tax Cookies', { size:16, bold:true, color:C.NAVY }),
      R('  ·  Income Tax Act 2025 — Key Summary', { size:16, color:C.MUTED }),
      R('\t' + query.substring(0,60), { size:16, italics:true, color:C.MUTED }),
    ],
  })],
});

var docFooter = new Footer({
  children: [new Paragraph({
    border: { top: { style:BorderStyle.SINGLE, size:2, color:C.LIGHT_RULE } },
    spacing: sp(80, 0),
    tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
    children: [
      R('Based on Actual 2025 Act Text', { size:16, italics:true, color:C.MUTED }),
      new TextRun({ text:'\tPage ', font:'Calibri', size:16, color:C.MUTED }),
      new TextRun({ children:[PageNumber.CURRENT], font:'Calibri', size:16, color:C.MUTED }),
    ],
  })],
});

// ─── Assemble document ────────────────────────────────────────────────────────
var children = [].concat(buildCover());

// If we have named sub-sections, render each with its own banner
var foundSubSections = false;
SUB_SECTIONS.forEach(function(sub) {
  // Find a matching key in subSections (partial match — tolerant of trailing punctuation)
  var matchedKey = Object.keys(subSections).find(function(k) {
    return k.toLowerCase().indexOf(sub.key.toLowerCase().substring(0, 12)) !== -1;
  });
  var content = matchedKey ? subSections[matchedKey] : null;
  if (content && content.trim()) {
    foundSubSections = true;
    children = children.concat(
      subSectionHeader(sub.key, sub.fill, sub.accent),
      buildProse(content, sub.accent),
      [ep(240)]
    );
  }
});

// Fallback: if sub-section splitting failed, dump full sec3 as a single block
if (!foundSubSections && sec3) {
  children = children.concat(
    subSectionHeader('Key Summary', 'E8EBF5', '1565C0'),
    buildProse(sec3, '1565C0'),
    [ep(240)]
  );
}

// End rule
children.push(new Paragraph({
  border: { top: { style:BorderStyle.SINGLE, size:4, color:C.NAVY } },
  spacing: sp(200, 100),
  alignment: AlignmentType.CENTER,
  children: [
    R('Tax Cookies', { font:'Georgia', size:20, bold:true, color:C.GOLD }),
    R('  Where Every Tax Query Finds an Answer ', { size:18, italics:true, color:C.MUTED }),
  ],
}));

var doc = new Document({
  numbering: { config: [] },
  styles: {
    default: {
      document: { run: { font:'Calibri', size:20, color: C.DARK } },
    },
  },
  sections: [{
    properties: {
      page: {
        size:   { width: PAGE_W, height: PAGE_H },
        margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
      },
    },
    headers: { default: docHeader },
    footers: { default: docFooter },
    children: children,
  }],
});

Packer.toBuffer(doc).then(function(buffer) {
  fs.writeFileSync(TEMP_DOCX, buffer);
  console.log('SUCCESS');
}).catch(function(err) {
  console.error('ERROR:', err.message);
  process.exit(1);
});
