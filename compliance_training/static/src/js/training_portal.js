/* eslint-disable */
document.addEventListener('DOMContentLoaded', function () {
    const canvas = document.getElementById('signatureCanvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    let isDrawing = false;
    let hasSigned = false; // <--- NEW: Tracks if signature exists
    let lastX = 0;
    let lastY = 0;

    function resizeCanvas() {
        const ratio = Math.max(window.devicePixelRatio || 1, 1);
        // Store existing data to prevent wiping on resize
        const data = canvas.toDataURL();
        canvas.width = canvas.offsetWidth * ratio;
        canvas.height = canvas.offsetHeight * ratio;
        ctx.scale(ratio, ratio);
        
        // Restore image if exists
        if (data && data !== 'data:,') {
            const img = new Image();
            img.src = data;
            img.onload = function() {
                ctx.drawImage(img, 0, 0, canvas.offsetWidth, canvas.offsetHeight);
            };
        }
    }
    // Delay slightly to ensure layout is done
    setTimeout(resizeCanvas, 100); 

    function startDraw(e) {
        if (e.type !== 'mousedown') e.preventDefault(); 
        isDrawing = true;
        const rect = canvas.getBoundingClientRect();
        if (e.touches && e.touches.length > 0) {
            lastX = e.touches[0].clientX - rect.left;
            lastY = e.touches[0].clientY - rect.top;
        } else {
            lastX = e.clientX - rect.left;
            lastY = e.clientY - rect.top;
        }
    }

    function draw(e) {
        if (!isDrawing) return;
        e.preventDefault();
        
        const rect = canvas.getBoundingClientRect();
        let x, y;
        if (e.touches && e.touches.length > 0) {
            x = e.touches[0].clientX - rect.left;
            y = e.touches[0].clientY - rect.top;
        } else {
            x = e.clientX - rect.left;
            y = e.clientY - rect.top;
        }

        ctx.beginPath();
        ctx.moveTo(lastX, lastY);
        ctx.lineTo(x, y);
        ctx.strokeStyle = '#000';
        ctx.lineWidth = 2;
        ctx.lineCap = 'round';
        ctx.stroke();
        
        lastX = x;
        lastY = y;
        
        hasSigned = true; // <--- NEW: Mark as signed when drawing happens
    }

    function endDraw(e) {
        isDrawing = false;
    }

    // Event Listeners
    canvas.addEventListener('mousedown', startDraw);
    canvas.addEventListener('mousemove', draw);
    canvas.addEventListener('mouseup', endDraw);
    canvas.addEventListener('mouseout', endDraw);
    canvas.addEventListener('touchstart', startDraw, {passive: false});
    canvas.addEventListener('touchmove', draw, {passive: false});
    canvas.addEventListener('touchend', endDraw);

    // Clear Button Logic
    const clearBtn = document.getElementById('clearBtn');
    if (clearBtn) {
        clearBtn.addEventListener('click', function() {
            // Clear the visible canvas
            ctx.clearRect(0, 0, canvas.width / window.devicePixelRatio, canvas.height / window.devicePixelRatio);
            hasSigned = false; // <--- NEW: Reset the signature flag
        });
    }

    // FORM SUBMISSION LOGIC
    const submitBtn = document.getElementById('submitBtn');
    if (submitBtn) {
        submitBtn.addEventListener('click', function(e) {
            // <--- NEW: validation check
            if (!hasSigned) {
                e.preventDefault(); // Stop everything
                alert("Please provide a signature before submitting.");
                return;
            }

            const dataUrl = canvas.toDataURL();
            
            // Populate the hidden field in the form
            const sigInput = document.getElementById('signatureInput');
            if (sigInput) {
                sigInput.value = dataUrl;
                document.getElementById('signForm').submit();
            } else {
                console.error("Error: signatureInput not found");
                alert("System Error: Could not find signature input field.");
            }
        });
    }
});