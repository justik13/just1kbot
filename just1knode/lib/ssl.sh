#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Библиотека управления SSL и маскировкой (lib/ssl.sh)
# =============================================================================

CERTBOT_DIR="${CERTBOT_DIR:-/var/www/certbot}"
WWW_HTML_DIR="${WWW_HTML_DIR:-/var/www/html}"
NGINX_CONF_DIR="${NGINX_CONF_DIR:-/etc/nginx}"

obtain_ssl_certificate() {
    local domain="$1"
    local email="$2"

    log "Получение SSL-сертификата Let's Encrypt для ${domain}..."
    mkdir -p "${CERTBOT_DIR}"

    if ! certbot certonly --webroot -w "${CERTBOT_DIR}" \
        -d "${domain}" \
        --email "${email}" \
        --agree-tos \
        --no-eff-email \
        --non-interactive \
        --keep-until-expiring; then
        error "Не удалось выпустить SSL-сертификат для ${domain}. Проверьте DNS A-запись и доступность порта 80."
    fi

    log "SSL-сертификат для ${domain} успешно получен!"
}

LETSENCRYPT_DIR="${LETSENCRYPT_DIR:-/etc/letsencrypt}"

deploy_camouflage_site() {
    mkdir -p "${WWW_HTML_DIR}"
    if [[ ! -f "${WWW_HTML_DIR}/index.html" ]]; then
        cat > "${WWW_HTML_DIR}/index.html" <<'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SimpleCalc — Online Calculator</title>
    <meta name="description" content="A clean, responsive, and easy-to-use online calculator for everyday math.">
    <style>
        :root {
            --bg-color: #0f172a;
            --surface: #1e293b;
            --surface-hover: #334155;
            --accent: #3b82f6;
            --accent-hover: #2563eb;
            --operator-bg: #f97316;
            --operator-hover: #ea580c;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --display-bg: #090d16;
            --border: #334155;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background-color: var(--bg-color); color: var(--text-main); min-height: 100vh; display: flex; flex-direction: column; justify-content: space-between; align-items: center; padding: 20px; }
        header { text-align: center; margin-bottom: 20px; }
        header h1 { font-size: 1.5rem; font-weight: 600; color: var(--text-main); }
        header p { font-size: 0.875rem; color: var(--text-muted); margin-top: 4px; }
        .calc-wrapper { background: var(--surface); border: 1px solid var(--border); border-radius: 24px; padding: 24px; width: 100%; max-width: 360px; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5); }
        .calc-display { background: var(--display-bg); border: 1px solid var(--border); border-radius: 16px; padding: 16px 20px; text-align: right; margin-bottom: 20px; min-height: 88px; display: flex; flex-direction: column; justify-content: flex-end; }
        .prev-operand { font-size: 0.9rem; color: var(--text-muted); min-height: 1.2rem; }
        .curr-operand { font-size: 2.2rem; font-weight: 600; color: var(--text-main); margin-top: 4px; }
        .calc-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
        button { border: none; background: var(--surface-hover); color: var(--text-main); font-size: 1.25rem; font-weight: 500; border-radius: 14px; height: 60px; cursor: pointer; transition: all 0.15s ease; display: flex; align-items: center; justify-content: center; }
        button:active { transform: scale(0.96); }
        button.btn-util { background: #475569; color: #f1f5f9; }
        button.btn-util:hover { background: #64748b; }
        button.btn-operator { background: var(--operator-bg); color: #fff; font-weight: 600; }
        button.btn-operator:hover { background: var(--operator-hover); }
        button.btn-num:hover { background: #475569; }
        button.btn-equals { background: var(--accent); color: #fff; font-weight: 600; }
        button.btn-equals:hover { background: var(--accent-hover); }
        button.span-2 { grid-column: span 2; }
        footer { margin-top: 30px; text-align: center; font-size: 0.8rem; color: var(--text-muted); }
        footer a { color: var(--text-muted); text-decoration: none; margin: 0 8px; }
        footer a:hover { color: var(--text-main); }
    </style>
</head>
<body>
    <header>
        <h1>SimpleCalc</h1>
        <p>Lightweight Online Calculator</p>
    </header>
    <div class="calc-wrapper">
        <div class="calc-display">
            <div class="prev-operand" id="prev-operand"></div>
            <div class="curr-operand" id="curr-operand">0</div>
        </div>
        <div class="calc-grid">
            <button class="btn-util" onclick="clearAll()">AC</button>
            <button class="btn-util" onclick="toggleSign()">±</button>
            <button class="btn-util" onclick="applyPercentage()">%</button>
            <button class="btn-operator" onclick="chooseOperation('/')">÷</button>
            <button class="btn-num" onclick="appendNumber('7')">7</button>
            <button class="btn-num" onclick="appendNumber('8')">8</button>
            <button class="btn-num" onclick="appendNumber('9')">9</button>
            <button class="btn-operator" onclick="chooseOperation('*')">×</button>
            <button class="btn-num" onclick="appendNumber('4')">4</button>
            <button class="btn-num" onclick="appendNumber('5')">5</button>
            <button class="btn-num" onclick="appendNumber('6')">6</button>
            <button class="btn-operator" onclick="chooseOperation('-')">−</button>
            <button class="btn-num" onclick="appendNumber('1')">1</button>
            <button class="btn-num" onclick="appendNumber('2')">2</button>
            <button class="btn-num" onclick="appendNumber('3')">3</button>
            <button class="btn-operator" onclick="chooseOperation('+')">+</button>
            <button class="btn-num span-2" onclick="appendNumber('0')">0</button>
            <button class="btn-num" onclick="appendNumber('.')">.</button>
            <button class="btn-equals" onclick="compute()">=</button>
        </div>
    </div>
    <footer>
        <p>&copy; 2026 SimpleCalc. All calculations done client-side.</p>
        <p style="margin-top: 6px;"><a href="#about">About</a> &bull; <a href="#privacy">Privacy</a> &bull; <a href="#terms">Terms</a></p>
    </footer>
    <script>
        let currentOperand = '0', previousOperand = '', operation = undefined;
        const prevEl = document.getElementById('prev-operand'), currEl = document.getElementById('curr-operand');
        function updateDisplay() {
            currEl.innerText = formatNumber(currentOperand);
            if (operation != null) {
                const s = operation === '*' ? '×' : (operation === '/' ? '÷' : operation);
                prevEl.innerText = `${formatNumber(previousOperand)} ${s}`;
            } else { prevEl.innerText = ''; }
        }
        function formatNumber(n) {
            if (!n) return '';
            const [i, d] = n.split('.');
            const f = isNaN(Number(i)) ? i : Number(i).toLocaleString('en-US');
            return d !== undefined ? `${f}.${d}` : f;
        }
        function appendNumber(num) {
            if (num === '.' && currentOperand.includes('.')) return;
            currentOperand = (currentOperand === '0' && num !== '.') ? num.toString() : currentOperand.toString() + num.toString();
            updateDisplay();
        }
        function chooseOperation(op) {
            if (currentOperand === '' && previousOperand === '') return;
            if (previousOperand !== '') compute();
            operation = op; previousOperand = currentOperand; currentOperand = '';
            updateDisplay();
        }
        function compute() {
            let res; const prev = parseFloat(previousOperand), curr = parseFloat(currentOperand);
            if (isNaN(prev) || isNaN(curr)) return;
            switch (operation) {
                case '+': res = prev + curr; break;
                case '-': res = prev - curr; break;
                case '*': res = prev * curr; break;
                case '/': res = curr === 0 ? 'Error' : prev / curr; break;
                default: return;
            }
            currentOperand = res.toString(); operation = undefined; previousOperand = '';
            updateDisplay();
        }
        function clearAll() { currentOperand = '0'; previousOperand = ''; operation = undefined; updateDisplay(); }
        function toggleSign() { if (currentOperand !== '0' && currentOperand !== '') { currentOperand = (parseFloat(currentOperand) * -1).toString(); updateDisplay(); } }
        function applyPercentage() { const c = parseFloat(currentOperand); if (!isNaN(c)) { currentOperand = (c / 100).toString(); updateDisplay(); } }
        window.addEventListener('keydown', (e) => {
            if ((e.key >= '0' && e.key <= '9') || e.key === '.') appendNumber(e.key);
            else if (e.key === '+') chooseOperation('+');
            else if (e.key === '-') chooseOperation('-');
            else if (e.key === '*' || e.key === 'x') chooseOperation('*');
            else if (e.key === '/') chooseOperation('/');
            else if (e.key === 'Enter' || e.key === '=') { e.preventDefault(); compute(); }
            else if (e.key === 'Escape' || e.key === 'c' || e.key === 'C') clearAll();
            else if (e.key === 'Backspace') { currentOperand = currentOperand.length > 1 ? currentOperand.slice(0, -1) : '0'; updateDisplay(); }
        });
    </script>
</body>
</html>
EOF
        log "Развернут камуфляжный сайт в ${WWW_HTML_DIR}/index.html"
    fi
}

deploy_certbot_renewal_hook() {
    mkdir -p "${LETSENCRYPT_DIR}/renewal-hooks/deploy"
    cat > "${LETSENCRYPT_DIR}/renewal-hooks/deploy/restart-xray-nginx.sh" <<'EOF'
#!/bin/bash
systemctl reload nginx 2>/dev/null || true
if [[ ! -f /etc/just1knode/traffic_cutoff.active ]]; then
    systemctl restart xray 2>/dev/null || true
    systemctl restart xray-api 2>/dev/null || true
fi
EOF
    chmod +x "${LETSENCRYPT_DIR}/renewal-hooks/deploy/restart-xray-nginx.sh"
}
