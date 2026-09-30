import "./options.css";
import { getToken, setToken } from "./api";

const input = document.getElementById("token") as HTMLInputElement;
const status = document.getElementById("status") as HTMLParagraphElement;
void getToken().then((token) => { input.value = token; });
document.getElementById("save")?.addEventListener("click", async () => { await setToken(input.value); status.textContent = "Token salvo neste navegador."; });
