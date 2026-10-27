import { Routes, Route, Navigate } from "react-router-dom";
import { Spin } from "antd";
import MainLayout from "./layouts/MainLayout";
import LoginPage from "./pages/Login";
import HomePage from "./pages/Home";
import DocumentList from "./pages/DocumentList";
import DocumentUpload from "./pages/DocumentUpload";
import DocumentDetail from "./pages/DocumentDetail";
import ReviewList from "./pages/ReviewList";
import ReviewWorkbench from "./pages/ReviewWorkbench";
import QuestionBank from "./pages/QuestionBank";
import QuestionDetail from "./pages/QuestionDetail";
import ExamList from "./pages/ExamList";
import ExamCreate from "./pages/ExamCreate";
import ExamDetail from "./pages/ExamDetail";
import StatisticsDashboard from "./pages/StatisticsDashboard";
import DeptStatistics from "./pages/DeptStatistics";
import ComplianceOverview from "./pages/ComplianceOverview";
import QuestionQuality from "./pages/QuestionQuality";
import SystemSettings from "./pages/SystemSettings";
import Profile from "./pages/Profile";
import MyExams from "./pages/MyExams";
import NotFound from "./pages/NotFound";
import { useAuthStore } from "./store/auth";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { token, loading } = useAuthStore();
  if (loading) return <Spin size="large" style={{ display: "block", margin: "200px auto" }} />;
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <MainLayout />
          </ProtectedRoute>
        }
      >
        <Route index element={<HomePage />} />
        <Route path="documents" element={<DocumentList />} />
        <Route path="documents/upload" element={<DocumentUpload />} />
        <Route path="documents/:id" element={<DocumentDetail />} />
        <Route path="review" element={<ReviewList />} />
        <Route path="review/:id" element={<ReviewWorkbench />} />
        <Route path="questions" element={<QuestionBank />} />
        <Route path="questions/:id" element={<QuestionDetail />} />
        <Route path="exams" element={<ExamList />} />
        <Route path="exams/create" element={<ExamCreate />} />
        <Route path="exams/:id" element={<ExamDetail />} />
        <Route path="statistics" element={<StatisticsDashboard />} />
        <Route path="statistics/dept" element={<DeptStatistics />} />
        <Route path="statistics/compliance" element={<ComplianceOverview />} />
        <Route path="statistics/question-quality" element={<QuestionQuality />} />
        <Route path="system" element={<SystemSettings />} />
        <Route path="profile" element={<Profile />} />
        <Route path="my-exams" element={<MyExams />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
