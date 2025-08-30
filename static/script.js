const chatBox = document.getElementById("chat-box");
const input = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");

// 🔹 Change this to your actual backend URL when deployed
const API_URL = "https://your-backend.onrender.com/chat";

sendBtn.addEventListener("click", sendMessage);
input.addEventListener("keydown", (e)=>{ if(e.key === "Enter"){ sendMessage(); }});

function addMessage(text, cls){
  const el = document.createElement("div");
  el.className = "message " + cls;
  el.innerText = text;
  chatBox.appendChild(el);
  chatBox.scrollTop = chatBox.scrollHeight;
  return el;
}

function addTypingIndicator(){
  const el = document.createElement("div");
  el.className = "message bot typing";
  el.innerText = "Bot is typing";
  const dots = document.createElement("span");
  dots.style.marginLeft = "6px";
  el.appendChild(dots);
  chatBox.appendChild(el);
  let i = 0;
  const int = setInterval(()=>{ dots.innerText = ".".repeat(i%4); i++; }, 400);
  chatBox.scrollTop = chatBox.scrollHeight;
  return {el, int};
}

function typeText(element, text, interval = 20){
  element.innerText = "";
  return new Promise((resolve)=>{
    let i = 0;
    const timer = setInterval(()=>{
      element.innerText += text.charAt(i);
      i++;
      chatBox.scrollTop = chatBox.scrollHeight;
      if(i >= text.length){ clearInterval(timer); resolve(); }
    }, interval);
  });
}

async function sendMessage(){
  const text = input.value.trim();
  if(!text) return;

  // Disable button while waiting
  sendBtn.disabled = true;
  sendBtn.style.backgroundColor = "#ccc"; // gray when disabled

  addMessage(text, "user");
  input.value = "";

  // show typing indicator while waiting for backend
  const typing = addTypingIndicator();
  try{
    const res = await fetch(API_URL, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({message: text})
    });
    const data = await res.json();
    clearInterval(typing.int);
    typing.el.remove();

    const botEl = document.createElement("div");
    botEl.className = "message bot";
    chatBox.appendChild(botEl);

    const replyText = data.answer || data.response || "No reply";
    // animate typing
    await typeText(botEl, replyText, 14);

    // optional source note
    const note = document.createElement("small");
    note.innerText = data.source ? ` (${data.source})` : "";
    botEl.appendChild(document.createElement("br"));
    botEl.appendChild(note);
    chatBox.scrollTop = chatBox.scrollHeight;

  }catch(err){
    clearInterval(typing.int);
    typing.el.remove();
    addMessage("⚠️ Error connecting to server: " + err.message, "bot");
  }

  // Re-enable button when bot is finished
  sendBtn.disabled = false;
  sendBtn.style.backgroundColor = ""; // restore normal color
}
