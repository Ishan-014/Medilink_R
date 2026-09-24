import {
  useEffect,
  useRef,
  useState,
} from "react";

import {
  Send,
  Paperclip,
  FileText,
  Trash2,
  BrainCircuit,
  ShieldCheck,
} from "lucide-react";

function Chatbot({
  patientId = null,
  compact = false,
  onUpload = null,
}) {
  const role = localStorage.getItem("role") || "doctor";

  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState([
    {
      sender: "bot",
      text:
        role === "doctor"
          ? "Clinical assistant ready. Ask about an EHR patient or upload a PDF/TXT document."
          : "Administrative assistant ready. Ask about appointments, insurance, contact information or upload an administrative document.",
    },
  ]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadedFile, setUploadedFile] = useState(null);

  const endRef = useRef(null);
  const fileRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading, uploading]);

  const sendMessage = async (suppliedMessage = null) => {
    const originalMessage = (suppliedMessage ?? message).trim();

    if (!originalMessage || loading) {
      return;
    }

    const token = localStorage.getItem("access_token");
    let backendMessage = originalMessage;

    const mentionsPatient = /\bMED\d{5}\b/i.test(backendMessage);
    const documentQuestion = /document|uploaded|file|pdf|txt/i.test(backendMessage);

    if (patientId && !mentionsPatient && !documentQuestion) {
      backendMessage = `${backendMessage} for ${patientId}`;
    }

    setMessages((previous) => [
      ...previous,
      {
        sender: "user",
        text: originalMessage,
      },
    ]);

    setMessage("");
    setLoading(true);

    try {
      const response = await fetch("http://127.0.0.1:8000/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ message: backendMessage }),
      });

      const data = await response.json();

      if (!response.ok) {
        setMessages((previous) => [
          ...previous,
          {
            sender: "restricted",
            text: data.detail || "Access to this information is restricted.",
          },
        ]);
        return;
      }

      setMessages((previous) => [
        ...previous,
        {
          sender: "bot",
          text: data.response || data.reply || "No response received.",
        },
      ]);
    } catch (error) {
      setMessages((previous) => [
        ...previous,
        {
          sender: "bot",
          text: "Unable to contact the MediLink assistant right now.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const uploadFile = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const lowerName = file.name.toLowerCase();
    if (!lowerName.endsWith(".pdf") && !lowerName.endsWith(".txt")) {
      setMessages((previous) => [
        ...previous,
        {
          sender: "bot",
          text: "Only PDF and TXT files are supported.",
        },
      ]);
      return;
    }

    const token = localStorage.getItem("access_token");
    setUploading(true);

    const form = new FormData();
    form.append("file", file);

    try {
      const response = await fetch("http://127.0.0.1:8000/chat/upload", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: form,
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Unable to process document.");
      }

      const uploaded = {
        name: data.filename,
        type: data.file_type,
        characters: data.characters_extracted,
      };

      setUploadedFile(uploaded);
      if (onUpload) onUpload(uploaded);

      setMessages((previous) => [
        ...previous,
        {
          sender: "file",
          text: `${data.filename} processed successfully`,
          detail: `${data.file_type} • ${data.characters_extracted} characters extracted`,
        },
      ]);
    } catch (error) {
      setMessages((previous) => [
        ...previous,
        {
          sender: "bot",
          text: "Unable to upload document.",
        },
      ]);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const suggestions =
    role === "doctor"
      ? [
          patientId ? `What is the diagnosis of ${patientId}?` : "What is the diagnosis of PAT-001?",
          patientId ? `What allergies does ${patientId} have?` : "What medication is PAT-001 taking?",
          "Summarize the uploaded document",
        ]
      : [
          patientId ? `When is ${patientId}'s next appointment?` : "When is PAT-001's next appointment?",
          patientId ? `What insurance does ${patientId} have?` : "What insurance does PAT-001 have?",
          "Summarize the uploaded document",
        ];

  return (
    <div className={compact ? "medilink-chat compact" : "medilink-chat"}>
      <div className="chat-header-new">
        <div className="chat-title-area">
          <div className="chat-logo">
            <BrainCircuit size={18} />
          </div>
          <div>
            <strong>MediLink Assistant</strong>
            <span>
              {role === "doctor" ? "Clinical Decision Support" : "Administrative Support"}
            </span>
          </div>
        </div>

        <div className="chat-status-indicator">MediLink AI • OpenAI • MCP Connected</div>

        <button className="icon-button" onClick={() => setMessages([])} title="Clear chat">
          <Trash2 size={17} />
        </button>
      </div>

      {patientId && (
        <div className="context-lock">
          <ShieldCheck size={14} />
          Context locked to
          <strong>{patientId}</strong>
        </div>
      )}

      {!compact && (
        <div className="chat-suggestions">
          {suggestions.map((suggestion) => (
            <button key={suggestion} onClick={() => sendMessage(suggestion)}>
              {suggestion}
            </button>
          ))}
        </div>
      )}

      {uploadedFile && (
        <div className="uploaded-document-chip">
          <FileText size={18} />
          <div>
            <strong>{uploadedFile.name}</strong>
            <span>
              {uploadedFile.type} • {uploadedFile.characters} characters extracted
            </span>
          </div>
        </div>
      )}

      <div className="chat-scroll">
        {messages.length === 0 && (
          <div className="chat-empty">Start a new MediLink AI conversation.</div>
        )}

        {messages.map((item, index) => {
          if (item.sender === "restricted") {
            return (
              <div key={index} className="access-message">
                <ShieldCheck size={18} />
                <div>
                  <strong>ACCESS RESTRICTED</strong>
                  <p>{item.text}</p>
                  <span>Role-based access control enforced</span>
                </div>
              </div>
            );
          }

          if (item.sender === "file") {
            return (
              <div key={index} className="chat-file-message">
                <FileText size={19} />
                <div>
                  <strong>{item.text}</strong>
                  <span>{item.detail}</span>
                </div>
              </div>
            );
          }

          return (
            <div
              key={index}
              className={item.sender === "user" ? "conversation-row user" : "conversation-row bot"}
            >
              {item.sender === "bot" && (
                <div className="mini-ai">
                  <BrainCircuit size={14} />
                </div>
              )}

              <div className="bubble">{item.text}</div>
            </div>
          );
        })}

        {(loading || uploading) && (
          <div className="conversation-row bot">
            <div className="mini-ai">
              <BrainCircuit size={14} />
            </div>
            <div className="bubble">
              {uploading ? "Processing document..." : "Checking MediLink records..."}
            </div>
          </div>
        )}

        <div ref={endRef} />
      </div>

      <div className="chat-composer">
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.txt"
          style={{ display: "none" }}
          onChange={uploadFile}
        />

        <button className="attachment-button" onClick={() => fileRef.current?.click()}>
          <Paperclip size={18} />
        </button>

        <textarea
          value={message}
          placeholder={
            role === "doctor"
              ? "Ask MediLink about patient records or an uploaded document..."
              : "Ask MediLink about appointments, insurance or an uploaded document..."
          }
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              sendMessage();
            }
          }}
        />

        <button className="send-button" onClick={() => sendMessage()}>
          <Send size={18} />
        </button>
      </div>
    </div>
  );
}

export default Chatbot;