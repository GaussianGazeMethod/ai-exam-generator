import { Routes, Route, Navigate } from "react-router-dom";
import MobileHome from "./pages/MobileHome";
import ExamConfirm from "./pages/ExamConfirm";
import ExamTaking from "./pages/ExamTaking";
import ExamResult from "./pages/ExamResult";

/**
 * 移动答题端路由。
 *
 * 注意：根路径必须有路由。此前只有 `/m/exam/...`，直接打开
 * http://localhost:5174 是纯白屏（用户会认为「端口打不开」）。
 * 移动端虽然设计为扫码直达，但仍需一个能看「我要考什么」的首页。
 */
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<MobileHome />} />
      <Route path="/m" element={<Navigate to="/" replace />} />
      <Route path="/m/exam/:examId" element={<ExamConfirm />} />
      <Route path="/m/exam/:examId/taking/:answerId" element={<ExamTaking />} />
      <Route path="/m/exam/:examId/result/:answerId" element={<ExamResult />} />
      <Route path="*" element={<MobileHome />} />
    </Routes>
  );
}
