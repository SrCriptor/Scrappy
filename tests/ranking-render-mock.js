const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('templates/index.html', 'utf8');
const functionStart = template.indexOf('function renderPublicRanking(rows) {');
const sidebarMarker = template.indexOf('/* ── Ranking Sidebar Drawer open / close ── */', functionStart);

if (functionStart < 0 || sidebarMarker < 0) {
    throw new Error('Could not locate the public ranking renderer in templates/index.html');
}

const functionEnd = template.lastIndexOf('\n}', sidebarMarker);
if (functionEnd < functionStart) {
    throw new Error('Could not find the end of the public ranking renderer');
}

const renderedList = {
    innerHTML: '',
    style: {},
    querySelectorAll: () => [],
};
const sandbox = {
    document: {
        getElementById: (id) => id === 'publicRankingList' ? renderedList : null,
    },
    window: { showRankTooltip() {} },
    VIP_LABELS: {
        0: 'VIP SUPREME',
        1: 'VIP DIAMANTE',
        2: 'VIP RUBI',
        3: 'VIP OURO',
        4: 'VIP PRATA',
        5: 'VIP BRONZE',
        6: 'VIP ESTELAR',
    },
    _escapeRankHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, (char) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[char]));
    },
    _avatarSrc() { return '/static/donors/donor_m_1.png'; },
    _vipBadge(vip) { return '<span class="vip-badge vip-' + vip + '">VIP</span>'; },
    _donorNameStyle() { return ''; },
};

vm.runInNewContext(template.slice(functionStart, functionEnd + 2), sandbox);

sandbox.renderPublicRanking([{
    id: 7,
    nickname: 'Apoiador Mock',
    avatar_id: 'donor_m_1',
    vip_level: 2,
    total_donated: 12.34,
    currency: 'BRL',
    discriminator: '0420',
    font_id: 'default',
    text_effect: 'solid',
    name_color: '#f3f4f6',
    online: false,
}]);

assert.match(renderedList.innerHTML, /Apoiador Mock/);
assert.match(renderedList.innerHTML, /data-donor-id="7"/);
assert.match(renderedList.innerHTML, /12<small>,34<\/small>/);
assert.equal(renderedList.style.display, 'block');

sandbox.renderPublicRanking([{
    id: 8,
    nickname: 'Sem valor',
    total_donated: 'inválido',
    vip_level: 6,
}]);
assert.match(renderedList.innerHTML, /Sem valor/);
assert.match(renderedList.innerHTML, /<span>0<\/span>/);

sandbox.renderPublicRanking(null);
assert.match(renderedList.innerHTML, /Não foi possível carregar o ranking/);

console.log('Isolated public ranking mock render: passed');
