// 各畫面的新手導覽內容（元件在 tour.js）。每段第一次進到那個畫面時播放一次。
import { maybeTour } from "./tour.js";

export const WELCOME = [
  { title: "歡迎來到 AI Interview", body: "在這裡跟 AI 面試官 Ava 用講的練習面試，結束後會拿到逐題評分和改善建議。花 30 秒帶你看一下怎麼用。", next: "開始導覽" },
  { el: ".setup .su-row:nth-child(1)", title: "先選要應徵的職缺", body: "貼上職缺內容（或網址），Ava 會依職缺的要求出題。之後可以加很多個職缺，各自練習。" },
  { el: ".setup .su-row:nth-child(2)", title: "上傳履歷（建議）", body: "有履歷的話，Ava 會針對你寫的專案追問細節，評分和建議也會更準。" },
  { el: ".start .btn.xl", title: "開始面試", body: "有兩種方式：「依題目」一題一題按麥克風回答；「Live 對談」像真人一樣自由聊、可以隨時插話。在「設定」那一行可以換。" },
  { el: 'a.nav-i[href="#report"]', side: true, title: "面試報告", body: "每場面試結束後，這裡有逐題分數、做得好的地方和「可以這樣說」的示範。" },
  { el: 'a.nav-i[href="#revise"]', side: true, title: "履歷修改", body: "Ava 會一題一題問出你做過、但履歷沒寫好的事，還能讀你的 GitHub，最後產生新版履歷並做初篩檢查。" },
  { el: 'a.nav-i[href="#career"]', side: true, title: "能力落差分析", body: "拿你的履歷比對想應徵的職缺，看還差什麼、該先補哪些，並排出可以打勾的準備計畫。" },
  { el: "#meBtn", side: true, title: "帳號與設定", body: "管理履歷、設定預設的面試方式。想再看一次這個導覽，也在這裡的「使用教學」。", done: "開始使用" },
];

export const INTERVIEW = [
  { el: ".tile", title: "面試官 Ava", body: "Ava 說話時頭貼外圈會亮，下面有即時字幕。右上角可以開自己的鏡頭（只在本機預覽，不會錄影）。" },
  { el: "#dock", title: "在這裡作答", body: "依題目模式按麥克風開始、回答完再按一次送出；Live 模式直接開口就好，想插話也可以。" },
  { el: ".ipanel", title: "題目與即時評分", body: "上面是目前的問題；每答完一題，「即時評分」會出現分數和改善建議，「對話紀錄」可以回頭看。" },
  { el: "#hRight", title: "暫停或結束", body: "暫停會停止計時，24 小時內都能回來繼續；按「結束面試」就會產生完整報告。", done: "知道了" },
];

export const REVISE = [
  { title: "履歷修改怎麼用", body: "像語言學習 App 開始前的卡片，一關一關問你，大多點一下就能回答，約 3–5 分鐘。分三個階段：", next: "看三個階段" },
  { title: "1. 設定", body: "選這次的目的、要改哪份履歷、想投的職缺（選了才會依職缺修），也可以讓 Ava 讀你的 GitHub 作品。" },
  { title: "2. 回答卡片", body: "Ava 比對履歷和職缺後，問出你做過但沒寫的事、缺的數字，也會建議可以刪掉的舊內容。都可以跳過。" },
  { title: "3. 新版履歷＋初篩檢查", body: "每一條改動都標出來，可以接受、還原或自己改；最上面有用 HR 和用人主管角度做的初篩檢查。完成後可以下載 Word 或存成新履歷。" },
  { el: ".rv-hero .btn.pri", title: "準備好就開始", body: "答到一半離開也沒關係，回來會接著上次那一題。", done: "知道了" },
];

export const CAREER = [
  { el: ".conds", title: "這次比對的條件", body: "履歷、職缺、作品集、求職意向都可以直接點，改完按「用這些條件重新分析」，結果頁不會被換掉。" },
  { el: "#hRight .hbtn.pri", title: "用一樣的條件重新分析", body: "履歷或作品更新後，按這裡用同樣的條件再分析一次；以前的結果都會保留在「歷次分析」。" },
  { el: ".gap-hero", title: "你離每個職缺多遠", body: "左邊是整體準備度，右邊是每個職缺的契合度，點了可以看詳細原因。" },
  { el: ".gap-tabs", title: "分頁看細節", body: "總覽看優勢和缺口；準備計畫是可以打勾的小項目；作品集依據是 Ava 實際從你的 GitHub 讀到什麼。", done: "知道了" },
];

export const tourWelcome = () => maybeTour("welcome", WELCOME, 600);
export const tourInterview = () => maybeTour("interview", INTERVIEW, 800);
export const tourRevise = () => maybeTour("revise", REVISE);
export const tourCareer = () => maybeTour("career", CAREER, 900);     // 等職缺契合度的長條動畫跑完
