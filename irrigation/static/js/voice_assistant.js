// Irrigation Planner - voice assistant.
//
// A small command bar on every project page. A spoken (or typed) phrase is
// matched against the workflow tabs and against the project summary served by
// /api/project/<id>/assistant/summary, then the page navigates or the answer is
// read out. Speech recognition uses the browser's Web Speech API; where it is
// missing the typed command path still works.
(function () {
    const root = document.getElementById('voiceAssistant');
    if (!root) return;

    const panel = document.getElementById('voiceAssistantPanel');
    const logBox = document.getElementById('voiceAssistantLog');
    const statusEl = document.getElementById('voiceAssistantStatus');
    const toggleBtn = document.getElementById('voiceAssistantToggle');
    const closeBtn = document.getElementById('voiceAssistantClose');
    const micBtn = document.getElementById('voiceAssistantMic');
    const form = document.getElementById('voiceAssistantForm');
    const input = document.getElementById('voiceAssistantText');

    const destinations = JSON.parse(root.dataset.destinations || '{}');
    const summaryUrl = root.dataset.summaryUrl;
    const currentEndpoint = root.dataset.current || '';
    const LANG_KEY = 'fis.voiceAssistant.lang';

    let lang = 'en-US';
    try { lang = localStorage.getItem(LANG_KEY) || 'en-US'; } catch (err) { /* private mode */ }

    let summary = null;
    let summaryRequest = null;
    let recognition = null;
    let listening = false;
    let navigating = false;

    // --- workflow topics ---------------------------------------------------
    // Longest matching phrase wins, so "zone pipe" beats "zone".
    const TOPICS = [
        { key: 'dripline', ar: ['خطوط التنقيط', 'خط التنقيط', 'التنقيط', 'خطوط تنقيط'],
          en: ['drip lines', 'drip line', 'driplines', 'dripline'] },
        { key: 'second_pipe', ar: ['الانبوب الثانوي', 'انبوب ثانوي', 'الانابيب الثانويه', 'الثانوي'],
          en: ['second pipe', 'secondary pipe', 'second pipes'] },
        { key: 'zone_pipe', ar: ['انابيب المناطق', 'انبوب المنطقه', 'انبوب منطقه'],
          en: ['zone pipes', 'zone pipe'] },
        { key: 'main_pipe', ar: ['الانبوب الرئيسي', 'انبوب رئيسي', 'الانابيب الرئيسيه'],
          en: ['main pipes', 'main pipe'] },
        { key: 'pressure_reduction', ar: ['خفض الضغط', 'خافض الضغط'],
          en: ['reduce pressure', 'pressure reduction', 'pressure reducer'] },
        { key: 'air_removal', ar: ['ازاله الهواء', 'تنفيس الهواء'],
          en: ['air removal', 'air release', 'air valve'] },
        { key: 'terminators', ar: ['النهايات', 'نهايه الخط'], en: ['terminators', 'terminator', 'end caps'] },
        { key: 'reductors', ar: ['المخفضات', 'مخفض'], en: ['reductors', 'reductor', 'reductions'] },
        { key: 'connectors', ar: ['الوصلات', 'وصله'], en: ['connectors', 'connector', 'couplings'] },
        { key: 'valves', ar: ['الصمامات', 'صمام'], en: ['valves', 'valve'] },
        { key: 'sectors', ar: ['القطاعات', 'القطاع', 'قطاع'], en: ['sectors', 'sector'] },
        { key: 'zones', ar: ['المناطق', 'المنطقه', 'منطقه'], en: ['zones', 'zone'] },
        { key: 'rows', ar: ['الصفوف', 'صفوف', 'صف'], en: ['tree rows', 'tree row', 'rows', 'row'] },
        { key: 'trees', ar: ['الاشجار', 'اشجار', 'شجره'], en: ['trees', 'tree'] },
        { key: 'water', ar: ['الموارد المائيه', 'مصدر المياه', 'البئر', 'الحوض', 'المياه'],
          en: ['water source', 'water', 'well', 'basin'] },
        { key: 'overview', ar: ['نظره عامه', 'الخريطه'], en: ['map overview', 'overview', 'map'] },
        { key: 'validation', ar: ['التحقق'], en: ['validation', 'validate', 'checks'] },
        { key: 'estimation', ar: ['التقدير', 'التسعير', 'السعر'], en: ['estimation', 'quote', 'pricing', 'price'] },
        { key: 'report', ar: ['التقارير', 'التقرير', 'تقرير'], en: ['reports', 'report'] },
        { key: 'project', ar: ['المشروع'], en: ['project'] }
    ];

    const PIPE_KEYS = ['main_pipe', 'second_pipe', 'zone_pipe', 'dripline'];
    const COUNT_KEYS = {
        sectors: 'sectors', zones: 'zones', rows: 'rows', trees: 'trees',
        main_pipe: 'main_pipe', second_pipe: 'second_pipe', zone_pipe: 'sub_pipe',
        dripline: 'dripline', valves: 'valves', connectors: 'connector',
        reductors: 'reduction', air_removal: 'air_release',
        pressure_reduction: 'pressure_reduction', terminators: 'terminator'
    };

    const NAMES = {
        en: {
            sectors: 'sectors', zones: 'zones', rows: 'tree rows', trees: 'trees',
            main_pipe: 'main pipes', second_pipe: 'second pipes',
            zone_pipe: 'zone pipes', dripline: 'drip lines', valves: 'valves',
            connectors: 'connectors', reductors: 'reductors',
            air_removal: 'air release valves', pressure_reduction: 'pressure reducers',
            terminators: 'terminators', project: 'project', water: 'water resource',
            overview: 'map overview', validation: 'validation', estimation: 'estimation',
            report: 'report', pipes: 'pipes', components: 'components'
        },
        ar: {
            sectors: 'القطاعات', zones: 'المناطق', rows: 'الصفوف', trees: 'الأشجار',
            main_pipe: 'الأنابيب الرئيسية', second_pipe: 'الأنابيب الثانوية',
            zone_pipe: 'أنابيب المناطق', dripline: 'خطوط التنقيط', valves: 'الصمامات',
            connectors: 'الوصلات', reductors: 'المخفضات',
            air_removal: 'صمامات إزالة الهواء', pressure_reduction: 'خافضات الضغط',
            terminators: 'النهايات', project: 'المشروع', water: 'الموارد المائية',
            overview: 'نظرة عامة', validation: 'التحقق', estimation: 'التقدير',
            report: 'التقرير', pipes: 'الأنابيب', components: 'المكونات'
        }
    };

    const COPY = {
        en: {
            listening: 'Listening…', ready: 'Ready', denied: 'Microphone permission denied',
            noSpeech: 'I did not catch that', unsupported: 'Voice input is not supported here — type below',
            answerCount: (n, label) => 'There are ' + formatNumber(n) + ' ' + label + '.',
            answerLength: (label, meters) => label + ': ' + formatNumber(meters) + ' m.',
            answerTotal: (meters) => 'Total pipe length: ' + formatNumber(meters) + ' m.',
            opening: (label) => 'Opening ' + label + '.',
            unknown: 'I did not understand that. Try “open drip line”, “how many zones” or “total pipe length”.',
            help: 'Try “open zone pipe”, “go to valves”, “how many trees”, “drip line length” or “total pipe length”.',
            here: (label) => 'You are on ' + label + '.'
        },
        ar: {
            listening: 'جارٍ الاستماع…', ready: 'جاهز', denied: 'تم رفض إذن الميكروفون',
            noSpeech: 'لم أسمع ذلك', unsupported: 'الإدخال الصوتي غير مدعوم هنا — اكتب في الأسفل',
            answerCount: (n, label) => 'عدد ' + label + ': ' + formatNumber(n) + '.',
            answerLength: (label, meters) => label + ': ' + formatNumber(meters) + ' متر.',
            answerTotal: (meters) => 'طول الأنابيب: ' + formatNumber(meters) + ' متر.',
            opening: (label) => 'جارٍ فتح ' + label + '.',
            unknown: 'لم أفهم الأمر. جرّب «افتح خطوط التنقيط» أو «كم عدد المناطق» أو «طول الأنابيب».',
            help: 'جرّب «افتح أنابيب المناطق» أو «اذهب إلى الصمامات» أو «كم عدد الأشجار» أو «طول الأنابيب».',
            here: (label) => 'أنت في ' + label + '.'
        }
    };

    function copy() {
        return COPY[lang.startsWith('ar') ? 'ar' : 'en'];
    }

    function labels() {
        return NAMES[lang.startsWith('ar') ? 'ar' : 'en'];
    }

    function formatNumber(value) {
        const number = Number(value) || 0;
        return number.toLocaleString('en-US', { maximumFractionDigits: 2 });
    }

    // --- normalisation -----------------------------------------------------

    function normalize(value) {
        return (value || '')
            .toLowerCase()
            .replace(/[\u064B-\u0652\u0670\u0640]/g, '')  // Arabic diacritics / tatweel
            .replace(/[\u0623\u0625\u0622]/g, '\u0627')   // alef forms
            .replace(/\u0649/g, '\u064A')                 // alef maqsura
            .replace(/\u0629/g, '\u0647')                 // ta marbuta
            .replace(/[^\p{L}\p{N}.\s]/gu, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function hasWord(text, phrase) {
        if (!phrase) return false;
        if (/[\u0600-\u06FF]/.test(phrase)) return text.includes(phrase);
        const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return new RegExp('(^|\\s)' + escaped + '($|\\s)').test(text);
    }

    function findTopic(text) {
        let best = null;
        let bestLength = 0;
        TOPICS.forEach(function (topic) {
            const phrases = [].concat(topic.en, topic.ar);
            phrases.forEach(function (phrase) {
                if (hasWord(text, phrase) && phrase.length > bestLength) {
                    best = topic;
                    bestLength = phrase.length;
                }
            });
        });
        return best ? best.key : null;
    }

    const COUNT_WORDS = ['how many', 'how much', 'count', 'number of', 'total number',
        'كم', 'عدد', 'كام'];
    const LENGTH_WORDS = ['length', 'long', 'meters', 'metres', 'metre', 'meter',
        'طول', 'الاطوال', 'امتار', 'متر'];
    const HELP_WORDS = ['help', 'commands', 'what can you do', 'مساعدة', 'مساعده', 'الاوامر', 'ماذا تستطيع'];

    function includesAny(text, words) {
        return words.some(function (word) { return hasWord(text, word); });
    }

    // --- data --------------------------------------------------------------

    function loadSummary() {
        if (summary) return Promise.resolve(summary);
        if (summaryRequest) return summaryRequest;
        summaryRequest = fetch(summaryUrl, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
            .then(function (response) {
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.json();
            })
            .then(function (data) {
                summary = data;
                return data;
            })
            .catch(function () {
                summaryRequest = null;
                return null;
            });
        return summaryRequest;
    }

    // --- replies -----------------------------------------------------------

    function say(message) {
        addLine('bot', message);
        speak(message);
    }

    function addLine(role, message) {
        const line = document.createElement('div');
        line.className = 'voice-assistant-line va-' + role;
        line.textContent = message;
        logBox.appendChild(line);
        logBox.scrollTop = logBox.scrollHeight;
    }

    function speak(message) {
        if (!window.speechSynthesis) return;
        try {
            window.speechSynthesis.cancel();
            const utterance = new SpeechSynthesisUtterance(message);
            utterance.lang = lang;
            const voice = (window.speechSynthesis.getVoices() || []).find(function (item) {
                return item.lang && item.lang.toLowerCase().startsWith(lang.slice(0, 2));
            });
            if (voice) utterance.voice = voice;
            window.speechSynthesis.speak(utterance);
        } catch (err) { /* speech is a bonus, never a blocker */ }
    }

    function go(key) {
        const url = destinations[key];
        if (!url) return false;
        say(copy().opening(labels()[key] || key));
        navigating = true;
        window.setTimeout(function () { window.location.href = url; }, 700);
        return true;
    }

    function answerCount(topic) {
        return loadSummary().then(function (data) {
            if (!data) {
                say(copy().unknown);
                return;
            }
            const counts = data.counts || {};
            const totals = data.totals || {};
            const labels_ = labels();
            const names = copy();

            if (!topic) {
                say(names.answerCount(totals.pipes || 0, labels_.pipes));
                return;
            }
            if (topic === 'components') {
                say(names.answerCount(totals.components || 0, labels_.components));
                return;
            }
            if (topic === 'valves') {
                say(names.answerCount(totals.valves || 0, labels_.valves));
                return;
            }
            const field = COUNT_KEYS[topic];
            if (!field) {
                say(names.unknown);
                return;
            }
            say(names.answerCount(counts[field] || 0, labels_[topic] || topic));
        });
    }

    function answerLength(topic) {
        return loadSummary().then(function (data) {
            if (!data) {
                say(copy().unknown);
                return;
            }
            const names = copy();
            const labels_ = labels();
            const lengths = data.lengths_m || {};
            if (topic && PIPE_KEYS.indexOf(topic) !== -1) {
                say(names.answerLength(labels_[topic], lengths[COUNT_KEYS[topic]] || 0));
                return;
            }
            const totals = data.totals || {};
            let message = names.answerTotal(totals.pipe_length_m || 0);
            if (lang.startsWith('en')) {
                const parts = PIPE_KEYS.map(function (key) {
                    return labels_[key] + ' ' + formatNumber(lengths[COUNT_KEYS[key]] || 0) + ' m';
                });
                message += ' (' + parts.join(', ') + ')';
            }
            say(message);
        });
    }

    function answerHere() {
        const names = copy();
        const tabNames = {
            'hydrology.main_pipe': 'main_pipe', 'hydrology.second_pipe': 'second_pipe',
            'hydrology.sub_pipe': 'zone_pipe', 'hydrology.dripline': 'dripline',
            'trees.rows_view': 'rows', 'trees.trees_view': 'trees',
            'components.valves': 'valves', 'components.connectors': 'connectors',
            'components.reductors': 'reductors', 'components.air_removal': 'air_removal',
            'components.pressure_reduction': 'pressure_reduction',
            'components.terminators': 'terminators', 'overview.index': 'overview',
            'engineering.validation_view': 'validation',
            'estimation.quote_view': 'estimation', 'engineering.report_view': 'report',
            'geometry.sectors_view': 'sectors', 'geometry.zones_view': 'zones',
            'water.well': 'water', 'project.detail': 'project'
        };
        const key = tabNames[currentEndpoint];
        say(names.here(key ? labels()[key] || key : labels().project));
    }

    // --- intent ------------------------------------------------------------

    function handle(rawText) {
        const text = normalize(rawText);
        if (!text) return;
        addLine('user', rawText.trim());

        if (includesAny(text, HELP_WORDS)) {
            say(copy().help);
            return;
        }
        if (includesAny(text, ['where am i', 'which page', 'current page', 'اين انا', 'اي صفحه'])) {
            answerHere();
            return;
        }

        const topic = findTopic(text);
        const wantsCount = includesAny(text, COUNT_WORDS);
        const wantsLength = includesAny(text, LENGTH_WORDS);
        const genericPipes = includesAny(text, ['pipes', 'pipe', 'الانابيب', 'انابيب']);

        if (wantsLength) {
            answerLength(topic);
            return;
        }
        if (wantsCount) {
            answerCount(topic || null);
            return;
        }
        if (topic) {
            if (go(topic)) return;
        }
        if (genericPipes && go('main_pipe')) return;
        say(copy().unknown);
    }

    // --- speech recognition ------------------------------------------------

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
        recognition = new SpeechRecognition();
        recognition.lang = lang;
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;
        recognition.continuous = false;

        recognition.addEventListener('result', function (event) {
            const transcript = Array.from(event.results || [])
                .map(function (result) { return result[0] ? result[0].transcript : ''; })
                .join(' ')
                .trim();
            if (transcript) handle(transcript);
        });
        recognition.addEventListener('error', function (event) {
            setStatus(event.error === 'not-allowed' ? copy().denied
                : event.error === 'no-speech' ? copy().noSpeech : copy().unknown);
        });
        recognition.addEventListener('end', function () {
            listening = false;
            micBtn.classList.remove('is-listening');
            if (!navigating) setStatus(copy().ready);
        });
    }

    function setStatus(message) {
        statusEl.textContent = message || '';
    }

    function startListening() {
        if (!recognition) {
            setStatus(copy().unsupported);
            return;
        }
        if (listening) {
            recognition.stop();
            return;
        }
        dialogueOpen();
        recognition.lang = lang;
        try {
            recognition.start();
            listening = true;
            micBtn.classList.add('is-listening');
            setStatus(copy().listening);
        } catch (err) {
            listening = false;
            micBtn.classList.remove('is-listening');
            setStatus(copy().unsupported);
        }
    }

    // --- panel -------------------------------------------------------------

    function dialogueOpen() {
        panel.hidden = false;
        root.classList.add('is-open');
        toggleBtn.setAttribute('aria-expanded', 'true');
    }

    function dialogueClose() {
        panel.hidden = true;
        root.classList.remove('is-open');
        toggleBtn.setAttribute('aria-expanded', 'false');
        if (recognition && listening) recognition.stop();
    }

    function setLang(next) {
        lang = next;
        try { localStorage.setItem(LANG_KEY, lang); } catch (err) { /* ignore */ }
        document.querySelectorAll('.va-lang').forEach(function (button) {
            button.classList.toggle('active', button.dataset.lang === lang);
        });
        if (recognition) recognition.lang = lang;
        setStatus(copy().ready);
    }

    document.querySelectorAll('.va-lang').forEach(function (button) {
        button.addEventListener('click', function () { setLang(button.dataset.lang); });
    });

    toggleBtn.addEventListener('click', function () {
        if (panel.hidden) {
            dialogueOpen();
            input.focus();
        } else {
            dialogueClose();
        }
    });
    closeBtn.addEventListener('click', dialogueClose);
    micBtn.addEventListener('click', startListening);

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        const value = input.value.trim();
        if (!value) return;
        input.value = '';
        handle(value);
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !panel.hidden && event.target !== input) dialogueClose();
    });

    setLang(lang);
})();
