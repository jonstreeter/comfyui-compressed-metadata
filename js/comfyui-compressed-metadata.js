// comfyui-compressed-metadata.js
// v2.5.4 — minimal fix: don't block native PNG drop unless we actually handle the file
(() => {
    const VERSION = "2.5.4";
    const EXIF_USER_COMMENT_PREFIX = "ASCII\0\0\0";
    const EXIF_COMPRESSED_PREFIX = "COMPRESSED:";
    const CDN = {
        ExifReader:
            "https://cdn.jsdelivr.net/npm/exifreader@4.23.1/dist/exif-reader.min.js",
        Pako: "https://cdn.jsdelivr.net/npm/pako@2.1.0/dist/pako.min.js",
    };

    let libsLoaded = false;
    const OriginalAlert = window.alert;

    const log = (...a) => console.log("[Compressed Metadata]", ...a);
    const warn = (...a) => console.warn("[Compressed Metadata]", ...a);
    const err = (...a) => console.error("[Compressed Metadata]", ...a);

    function suppressAlert() {
        if (window.alert === OriginalAlert) {
            window.alert = (msg) => log("(suppressed alert)", msg);
        }
    }
    function restoreAlert() { window.alert = OriginalAlert; }

    function scriptAlreadyLoaded(url) {
        return Array.from(document.scripts).some((s) => s.src === url);
    }
    function loadScript(url) {
        return new Promise((resolve, reject) => {
            if (scriptAlreadyLoaded(url)) return resolve();
            const s = document.createElement("script");
            s.src = url;
            s.async = true;
            s.onload = () => resolve();
            s.onerror = (e) => reject(e);
            document.head.appendChild(s);
        });
    }
    async function loadLibs() {
        if (libsLoaded) return;
        await loadScript(CDN.ExifReader);
        await loadScript(CDN.Pako);
        if (!(window.ExifReader && window.pako)) {
            throw new Error("ExifReader or pako not present after load.");
        }
        libsLoaded = true;
        log("External libs loaded.");
    }
    const libsReadyPromise = (async () => {
        try { await loadLibs(); } catch { /* retry on demand */ }
    })();

    function decompressBase64ToJSON(b64) {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const inflated = window.pako.inflate(bytes);
        const json = new TextDecoder().decode(inflated);
        return JSON.parse(json);
    }
    function cleanExifUserComment(str) {
        if (!str) return "";
        str = str.replace(/\x00+$/g, "");
        if (str.startsWith(EXIF_USER_COMMENT_PREFIX)) {
            str = str.slice(EXIF_USER_COMMENT_PREFIX.length);
        }
        return str;
    }

    async function extractWorkflowFromImageFile(file) {
        await libsReadyPromise;
        await loadLibs();

        const buf = await file.arrayBuffer();
        let tags = {};
        try {
            tags = await ExifReader.load(buf, { expanded: true });
        } catch (e) {
            warn("EXIF load failed; trying legacy PNG fallback if PNG.", e);
        }

        const uc =
            tags?.exif?.UserComment?.description ||
            tags?.UserComment?.description ||
            null;

        if (uc) {
            let s = cleanExifUserComment(uc);
            if (s.startsWith(EXIF_COMPRESSED_PREFIX)) {
                try {
                    return decompressBase64ToJSON(s.slice(EXIF_COMPRESSED_PREFIX.length));
                } catch (e) {
                    err("Decompression failed from EXIF UserComment.", e);
                }
            } else {
                try { return JSON.parse(s); } catch { /* ignore */ }
            }
        }

        // Legacy PNG fallback – if we end up here, we'll let native handle by default.
        const legacy = extractLegacyPngWorkflow(buf);
        if (legacy) return legacy;

        return null;
    }

    function extractLegacyPngWorkflow(arrayBuffer) {
        try {
            const txt = new TextDecoder().decode(new Uint8Array(arrayBuffer));
            const match = txt.match(/\{[\s\S]*\}/);
            if (match) {
                const obj = JSON.parse(match[0]);
                if (obj?.workflow) return obj.workflow;
                if (obj?.prompt?.workflow) return obj.prompt.workflow;
                if (obj?.nodes && (obj?.links || obj?.edges)) return obj;
                return obj;
            }
        } catch { /* ignore */ }
        return null;
    }

    function patchGetPromptOnce() {
        const app = window.app;
        if (!app || !app.getPrompt || app.__compressedMetadataPatched) return;

        const original = app.getPrompt.bind(app);
        app.getPrompt = function (...args) {
            try {
                const prompt = original(...args);
                const graph = app.graph.serialize();
                prompt.extra_pnginfo = prompt.extra_pnginfo || {};
                prompt.extra_pnginfo.workflow = graph;
                return prompt;
            } catch (e) {
                err("getPrompt patch error:", e);
                return original(...args);
            }
        };
        app.__compressedMetadataPatched = true;
        log("Patched app.getPrompt to inject workflow into extra_pnginfo.");
    }

    function isEventOverCanvasElement(e, canvas) {
        if (!canvas) return false;
        const t = e.target;
        return t === canvas.canvas || t === canvas.bgcanvas;
    }

    function getCanvasCoords(e, canvas) {
        if (canvas && typeof canvas.convertEventToCanvasOffset === "function") {
            try { return canvas.convertEventToCanvasOffset(e); } catch { }
        }
        const rect = canvas?.canvas?.getBoundingClientRect?.();
        if (rect) {
            const localX = e.clientX - rect.left;
            const localY = e.clientY - rect.top;
            const ds = canvas?.ds;
            if (ds && typeof ds.scale === "number" && Array.isArray(ds.offset)) {
                const s = ds.scale || 1;
                const ox = ds.offset[0] || 0;
                const oy = ds.offset[1] || 0;
                return [localX / s - ox, localY / s - oy];
            }
            return [localX, localY];
        }
        return [e.clientX, e.clientY];
    }

    function findNodeUnderCanvasCoords(cx, cy, canvas) {
        if (!canvas || !canvas.graph) return null;

        if (typeof canvas.getNodeOnPos === "function") {
            try {
                const pool = canvas.visible_nodes || canvas.graph?._nodes;
                const n = canvas.getNodeOnPos(cx, cy, pool);
                if (n) return n;
            } catch { }
        }
        if (typeof canvas.getNodeUnderMouse === "function") {
            try {
                const n = canvas.getNodeUnderMouse(cx, cy);
                if (n) return n;
            } catch { }
        }

        const list = canvas.visible_nodes || canvas.graph?._nodes || [];
        for (let i = list.length - 1; i >= 0; i--) {
            const node = list[i];
            try {
                if (typeof node.isPointInside === "function") {
                    if (node.isPointInside(cx, cy, 0)) return node;
                } else if (node.pos && node.size) {
                    const [nx, ny] = node.pos;
                    const [w, h] = node.size;
                    if (cx >= nx && cy >= ny && cx <= nx + w && cy <= ny + h) return node;
                }
            } catch { }
        }
        return null;
    }

    // (We’re keeping naming disabled per your preference; leaving helper in case needed later)
    function applyWorkflowNameWithRetry(base, maxMs = 1200, stepMs = 120) {
        const t0 = performance.now();
        const attempt = () => {
            const g = window.app?.graph;
            if (g) {
                try {
                    g.extra = g.extra || {};
                    g.extra.workflow_name = base;
                    g.extra.name = base;
                } catch { }
            }
            if (performance.now() - t0 >= maxMs) return;
            setTimeout(attempt, stepMs);
        };
        queueMicrotask(() => requestAnimationFrame(attempt));
    }

    function installDropHandlers() {
        const doc = document;

        doc.addEventListener("dragover", (e) => {
            if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
        }, { capture: true, passive: true });

        doc.addEventListener("drop", async (e) => {
            const dt = e.dataTransfer;
            if (!dt || !dt.files || dt.files.length === 0) return;

            const canvas = window.app?.canvas;
            const overCanvasEl = isEventOverCanvasElement(e, canvas);

            let nodeUnderMouse = null;
            let cx = 0, cy = 0;
            if (canvas && overCanvasEl) {
                [cx, cy] = getCanvasCoords(e, canvas);
                nodeUnderMouse = findNodeUnderCanvasCoords(cx, cy, canvas);
            }

            log(
                `Hit-test @ ${Math.round(cx)},${Math.round(cy)} =>`,
                nodeUnderMouse ? `${nodeUnderMouse.type || nodeUnderMouse.comfyClass}#${nodeUnderMouse.id}` : "none"
            );

            // If dropping on a node or not over canvas, let native handlers run.
            if (nodeUnderMouse) return;
            if (!overCanvasEl) return;

            // IMPORTANT: Do NOT preventDefault yet.
            // We'll only block native behavior if we actually handle a compressed workflow.

            // Try to detect and load our compressed workflow
            try {
                await libsReadyPromise;
                await loadLibs();
            } catch (libErr) {
                err("Library loading failed; cannot process background drop.", libErr);
                // Don’t block native
                return;
            }

            const file = dt.files[0];
            try {
                const workflow = await extractWorkflowFromImageFile(file);
                if (workflow) {
                    // Now that we know we'll handle it, block native and load ours.
                    e.preventDefault();
                    e.stopPropagation();
                    suppressAlert();

                    log("Compressed/embedded workflow found → loading to canvas.");
                    window.app?.loadGraphData?.(workflow);

                    // (You chose to keep the default title; leaving name code disabled)
                    // const base = (file?.name || "unsaved").replace(/\.(png|jpe?g|webp|bmp|gif|tiff?)$/i, "");
                    // applyWorkflowNameWithRetry(base);

                    restoreAlert();
                } else {
                    // No compressed metadata → let ComfyUI natively handle PNG/json text
                    log("No embedded compressed workflow; deferring to native drop.");
                }
            } catch (ex) {
                // On any error, do not block native
                err("Error extracting workflow from dropped file (will defer to native):", ex);
            }
        }, { capture: true });

        log("Drop handlers installed.");
    }

    function setupOnce() {
        if (window.__compressedMetadataSetupDone) return;
        window.__compressedMetadataSetupDone = true;

        const tryPatch = () => {
            if (window.app && window.app.graph) {
                patchGetPromptOnce();
            } else {
                setTimeout(tryPatch, 200);
            }
        };
        tryPatch();

        installDropHandlers();
        log(`Initialized v${VERSION}`);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", setupOnce, { once: true });
    } else {
        setupOnce();
    }
})();
