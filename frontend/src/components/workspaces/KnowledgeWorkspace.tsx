import React, { useState, useMemo } from 'react'
import {
  Search,
  RefreshCw,
  ExternalLink,
  Sparkles,
  AlertCircle,
  FileText,
  ArrowLeft,
  Eye,
  CheckCircle2,
} from 'lucide-react'
import { apiClient } from '../../api/client'
import type { RAGCitation } from '../../types/api'
import { MarkdownContent } from '../ui/MarkdownContent'
import './Workspaces.css'

interface KnowledgeWorkspaceProps {
  onSelectCitation: (citation: RAGCitation) => void
}

interface InternalDoc {
  id: string
  code: string
  title: string
  category: 'policy' | 'finance' | 'hr' | 'tech'
  categoryLabel: string
  effectiveDate: string
  authority: string
  summary: string
  pages: number
  samplePrompt: string
}

/**
 * Danh mục văn bản bám sát kho RAG thật: 37 quyết định/chỉ thị đã số hóa trong
 * `data/QuyetDinh` (số hiệu, ngày ký, số trang OCR đều lấy từ file nguồn).
 * Mọi thẻ đều có văn bản đối ứng trong DB nên nút "Tra cứu"/"Tóm tắt" luôn
 * truy xuất được evidence thay vì trả lời "không có văn bản".
 */
const INTERNAL_DOCUMENTS: InternalDoc[] = [
  // ---- Quy chế & Chỉ đạo (policy) ----
  {
    id: 'doc-01',
    code: '1838/CT-TNVN',
    title: 'Chỉ thị tổ chức Diễn đàn trực tuyến về EVFTA',
    category: 'policy',
    categoryLabel: 'Quy chế & Chỉ đạo',
    effectiveDate: '23/07/2020',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Chỉ đạo tổ chức Diễn đàn trực tuyến “EVFTA – con đường đặc lợi, con đường gian nan” phối hợp Liên hiệp các Hội Doanh nghiệp VN tại châu Âu.',
    pages: 2,
    samplePrompt: 'Tóm tắt nội dung Chỉ thị 1838 về tổ chức Diễn đàn trực tuyến EVFTA',
  },
  {
    id: 'doc-02',
    code: '1367/QĐ-TNVN',
    title: 'Quy chế tổ chức Liên hoan Phát thanh toàn quốc',
    category: 'policy',
    categoryLabel: 'Quy chế & Chỉ đạo',
    effectiveDate: '10/06/2024',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Ban hành Quy chế tổ chức Liên hoan Phát thanh toàn quốc (kèm chế độ nhuận bút, kinh phí giải báo chí).',
    pages: 10,
    samplePrompt: 'Tóm tắt nội dung Quyết định 1367 về Quy chế tổ chức Liên hoan Phát thanh toàn quốc',
  },
  {
    id: 'doc-03',
    code: '29/QĐ-TNVN',
    title: 'Quy chế làm việc của Đài Tiếng nói Việt Nam',
    category: 'policy',
    categoryLabel: 'Quy chế & Chỉ đạo',
    effectiveDate: '21/04/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Quy chế làm việc: kỷ luật phát ngôn, bảo mật, tổ chức hội nghị – cuộc họp, trách nhiệm đơn vị và cá nhân.',
    pages: 24,
    samplePrompt: 'Tóm tắt nội dung Quyết định 29 về Quy chế làm việc của Đài Tiếng nói Việt Nam',
  },
  {
    id: 'doc-04',
    code: '56/QĐ-TNVN',
    title: 'Quy chế công tác văn thư và lưu trữ',
    category: 'policy',
    categoryLabel: 'Quy chế & Chỉ đạo',
    effectiveDate: '23/04/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Quản lý văn bản đi/đến, con dấu, chữ ký số; bảo vệ bí mật nhà nước và an toàn thông tin mạng hệ thống QLVB.',
    pages: 57,
    samplePrompt: 'Tóm tắt nội dung Quyết định 56 về Quy chế công tác văn thư và lưu trữ',
  },
  {
    id: 'doc-05',
    code: '587/QĐ-TNVN',
    title: 'Quy chế chấm điểm Liên hoan Phát thanh toàn quốc XVII',
    category: 'policy',
    categoryLabel: 'Quy chế & Chỉ đạo',
    effectiveDate: '16/03/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Quy chế chấm điểm các tác phẩm dự Liên hoan Phát thanh toàn quốc lần XVII – Quảng Ninh 2026.',
    pages: 7,
    samplePrompt: 'Tóm tắt nội dung Quyết định 587 về Quy chế chấm điểm Liên hoan Phát thanh',
  },
  // ---- Tài chính & Dự toán (finance) ----
  {
    id: 'doc-06',
    code: '427/QĐ-TNVN',
    title: 'Dự toán hoạt động thông tin khoa học năm 2026',
    category: 'finance',
    categoryLabel: 'Tài chính & Dự toán',
    effectiveDate: '25/02/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Phê duyệt dự toán hoạt động thông tin khoa học Đài TNVN năm 2026.',
    pages: 4,
    samplePrompt: 'Tóm tắt nội dung Quyết định 427 về dự toán hoạt động thông tin khoa học năm 2026',
  },
  {
    id: 'doc-07',
    code: '4332/QĐ-TNVN',
    title: 'Chương trình tiết kiệm, chống lãng phí năm 2026',
    category: 'finance',
    categoryLabel: 'Tài chính & Dự toán',
    effectiveDate: '31/12/2025',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Chương trình tiết kiệm, chống lãng phí: quản lý, sử dụng lao động và thời gian lao động.',
    pages: 9,
    samplePrompt: 'Tóm tắt nội dung Quyết định 4332 về Chương trình tiết kiệm, chống lãng phí năm 2026',
  },
  // ---- Nhân sự & Đào tạo (hr) ----
  {
    id: 'doc-08',
    code: '80/QĐ-TNVN',
    title: 'Chấm dứt hợp đồng làm việc đối với viên chức',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '28/04/2026',
    authority: 'Đảng ủy Đài TNVN',
    summary: 'Quyết định chấm dứt hợp đồng làm việc đối với viên chức.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 80 về chấm dứt hợp đồng làm việc viên chức',
  },
  {
    id: 'doc-09',
    code: '109/QĐ-TNVN',
    title: 'Tuyển dụng viên chức',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '05/05/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Quyết định tuyển dụng viên chức Đài TNVN.',
    pages: 2,
    samplePrompt: 'Tóm tắt nội dung Quyết định 109 về tuyển dụng viên chức',
  },
  {
    id: 'doc-10',
    code: '23/QĐ-TNVN',
    title: 'Chức năng, nhiệm vụ, tổ chức bộ máy Trung tâm R&D',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '21/04/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Quy định vị trí, chức năng tham mưu NCKH, chuyển đổi số và an toàn thông tin mạng của Trung tâm R&D.',
    pages: 4,
    samplePrompt: 'Tóm tắt nội dung Quyết định 23 về chức năng, nhiệm vụ Trung tâm R&D',
  },
  {
    id: 'doc-11',
    code: '808/QĐ-TNVN',
    title: 'Điều động cán bộ cấp phòng Trung tâm Kỹ thuật',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '30/03/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Điều động ông Lưu Phú giữ chức Trưởng phòng Phòng Quản lý kỹ thuật, Trung tâm Kỹ thuật.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 808 về điều động ông Lưu Phú giữ Trưởng phòng Quản lý kỹ thuật',
  },
  {
    id: 'doc-12',
    code: '862/QĐ-TNVN',
    title: 'Chế độ thâm niên vượt khung năm 2026',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '31/03/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Thực hiện chế độ thâm niên vượt khung năm 2026 đối với viên chức.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 862 về chế độ thâm niên vượt khung năm 2026',
  },
  {
    id: 'doc-13',
    code: '906/QĐ-TNVN',
    title: 'Thôi giữ chức vụ quản lý Trung tâm Kỹ thuật',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '31/03/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Ông Nguyễn Hoàng Mạnh thôi giữ chức Phó Trưởng phòng Quản lý kỹ thuật, hưởng phụ cấp đến 31/01/2029.',
    pages: 2,
    samplePrompt: 'Tóm tắt nội dung Quyết định 906 về việc ông Nguyễn Hoàng Mạnh thôi giữ chức vụ',
  },
  {
    id: 'doc-14',
    code: '937/QĐ-TNVN',
    title: 'Tuyển dụng viên chức',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '31/03/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Quyết định tuyển dụng viên chức Đài TNVN.',
    pages: 2,
    samplePrompt: 'Tóm tắt nội dung Quyết định 937 về tuyển dụng viên chức',
  },
  {
    id: 'doc-15',
    code: '627/QĐ-TNVN',
    title: 'Bổ nhiệm lại cán bộ',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '18/03/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Quyết định bổ nhiệm lại cán bộ Đài TNVN.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 627 về bổ nhiệm lại cán bộ',
  },
  {
    id: 'doc-16',
    code: '664/QĐ-TNVN',
    title: 'Tặng quà nữ viên chức, người lao động 8/3/2026',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '20/03/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Tặng quà cho nữ viên chức, người lao động nhân ngày Quốc tế Phụ nữ 8/3/2026.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 664 về tặng quà ngày 8/3 cho nữ viên chức',
  },
  {
    id: 'doc-17',
    code: '683/QĐ-TNVN',
    title: 'Thành lập Tổ Kỹ thuật phục vụ Liên hoan Phát thanh',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '24/03/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Thành lập Tổ Kỹ thuật phục vụ Liên hoan Phát thanh toàn quốc lần XVII – Quảng Ninh 2026.',
    pages: 3,
    samplePrompt: 'Tóm tắt nội dung Quyết định 683 về thành lập Tổ Kỹ thuật phục vụ Liên hoan Phát thanh',
  },
  {
    id: 'doc-18',
    code: '1139/QĐ-TNVN',
    title: 'Danh mục mã định danh điện tử các đơn vị',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '16/04/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Ban hành danh mục mã định danh điện tử (A71) phục vụ kết nối, chia sẻ dữ liệu.',
    pages: 6,
    samplePrompt: 'Tóm tắt nội dung Quyết định 1139 về mã định danh điện tử các đơn vị',
  },
  {
    id: 'doc-19',
    code: '899/QĐ-TNVN',
    title: 'Gia hạn chế độ phu nhân đối với viên chức',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '31/03/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Gia hạn hưởng chế độ phu nhân đối với viên chức công tác nhiệm kỳ.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 899 về gia hạn chế độ phu nhân viên chức',
  },
  {
    id: 'doc-20',
    code: '87/QĐ-TNVN',
    title: 'Tập huấn “Ứng dụng AI trong tòa soạn”',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '29/04/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Tổ chức khóa tập huấn nghiệp vụ ứng dụng AI trong tòa soạn.',
    pages: 2,
    samplePrompt: 'Tóm tắt nội dung Quyết định 87 về khóa tập huấn ứng dụng AI trong tòa soạn',
  },
  {
    id: 'doc-21',
    code: '50/QĐ-TNVN',
    title: 'Cử viên chức học lớp bồi dưỡng ngạch chuyên viên 2026',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '23/04/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Cử viên chức tham gia lớp bồi dưỡng ngạch chuyên viên và chuyên viên chính năm 2026.',
    pages: 3,
    samplePrompt: 'Tóm tắt nội dung Quyết định 50 về cử viên chức học lớp bồi dưỡng ngạch chuyên viên',
  },
  {
    id: 'doc-22',
    code: 'QĐ-CVC/TNVN',
    title: 'Cử viên chức học lớp bồi dưỡng ngạch chuyên viên 2026 (văn bản CVC)',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '25/04/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Cử viên chức tham gia lớp bồi dưỡng ngạch chuyên viên và chuyên viên chính năm 2026.',
    pages: 3,
    samplePrompt: 'Tóm tắt nội dung văn bản về cử viên chức tham gia lớp bồi dưỡng ngạch chuyên viên chính',
  },
  {
    id: 'doc-23',
    code: '445/QĐ-TNVN',
    title: 'Công nhận Chiến sĩ thi đua cơ sở năm 2025',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '27/02/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Công nhận danh hiệu Chiến sĩ thi đua cơ sở năm 2025.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 445 về công nhận Chiến sĩ thi đua cơ sở năm 2025',
  },
  {
    id: 'doc-24',
    code: '90/QĐ-TNVN',
    title: 'Tặng Bằng khen của Tổng Giám đốc',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '16/01/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Tặng Bằng khen của Tổng Giám đốc theo Quy chế Thi đua, khen thưởng.',
    pages: 2,
    samplePrompt: 'Tóm tắt nội dung Quyết định 90 về tặng Bằng khen của Tổng Giám đốc',
  },
  {
    id: 'doc-25',
    code: '91/QĐ-TNVN',
    title: 'Tặng Bằng khen của Tổng Giám đốc',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '16/01/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Tặng Bằng khen của Tổng Giám đốc theo Quy chế Thi đua, khen thưởng.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 91 về tặng Bằng khen của Tổng Giám đốc',
  },
  {
    id: 'doc-26',
    code: '93/QĐ-TNVN',
    title: 'Tặng Bằng khen của Tổng Giám đốc',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '16/01/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Tặng Bằng khen của Tổng Giám đốc theo Quy chế Thi đua, khen thưởng.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 93 về tặng Bằng khen của Tổng Giám đốc',
  },
  {
    id: 'doc-27',
    code: '1119/QĐ-TNVN',
    title: 'Tặng Bằng khen của Tổng Giám đốc',
    category: 'hr',
    categoryLabel: 'Nhân sự & Đào tạo',
    effectiveDate: '13/04/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Tặng Bằng khen của Tổng Giám đốc theo Quy chế Thi đua, khen thưởng.',
    pages: 2,
    samplePrompt: 'Tóm tắt nội dung Quyết định 1119 về tặng Bằng khen của Tổng Giám đốc',
  },
  // ---- Khoa học & Phát sóng (tech) ----
  {
    id: 'doc-28',
    code: '1792/QĐ-TNVN',
    title: 'Thành lập Hội đồng tư vấn xác định đề tài NCKH',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '02/06/2025',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Thành lập Hội đồng tư vấn xác định đề tài nghiên cứu khoa học tại Đài TNVN.',
    pages: 2,
    samplePrompt: 'Tóm tắt nội dung Quyết định 1792 về Hội đồng tư vấn xác định đề tài nghiên cứu khoa học',
  },
  {
    id: 'doc-29',
    code: '3698/QĐ-TNVN',
    title: 'Nghiệm thu đề tài xác thực đa nhân tố nền tảng số',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '07/11/2025',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Thành lập Hội đồng nghiệm thu đề tài “Giải pháp xác thực đa nhân tố cho nền tảng số” (R&D, Bùi Thế Anh).',
    pages: 3,
    samplePrompt: 'Tóm tắt nội dung Quyết định 3698 về nghiệm thu đề tài xác thực đa nhân tố',
  },
  {
    id: 'doc-30',
    code: '3631/QĐ-TNVN',
    title: 'Giao nhiệm vụ NCKH 2025: trường quay trực tuyến qua IP',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '31/12/2024',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Giao đề tài xây dựng giải pháp trường quay trực tuyến qua IP cho đào tạo.',
    pages: 3,
    samplePrompt: 'Tóm tắt nội dung Quyết định 3631 về đề tài trường quay trực tuyến qua IP',
  },
  {
    id: 'doc-31',
    code: '4348/QĐ-TNVN',
    title: 'Điều chỉnh dự toán và giao nhiệm vụ NCKH 2026',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '31/12/2025',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Điều chỉnh dự toán và giao nhiệm vụ nghiên cứu khoa học năm 2026.',
    pages: 12,
    samplePrompt: 'Tóm tắt nội dung Quyết định 4348 về điều chỉnh dự toán và giao nhiệm vụ nghiên cứu khoa học 2026',
  },
  {
    id: 'doc-32',
    code: '4357/QĐ-TNVN',
    title: 'Giao nhiệm vụ NCKH 2026: Thương hiệu quốc gia qua phát thanh',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '31/12/2025',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Giao đề tài “Phát triển Thương hiệu quốc gia qua phát thanh và truyền thông số” (R&D, Nguyễn Vũ Duy).',
    pages: 4,
    samplePrompt: 'Tóm tắt nội dung Quyết định 4357 về đề tài phát triển Thương hiệu quốc gia qua phát thanh',
  },
  {
    id: 'doc-33',
    code: '4351/QĐ-TNVN',
    title: 'Giao nhiệm vụ NCKH 2026: trợ lý ảo cung cấp thông tin',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '31/12/2025',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary:
      'Giao đề tài “trợ lý ảo hỗ trợ cung cấp thông tin” (R&D, Cao Hòa Bình, kinh phí 350 triệu đồng).',
    pages: 4,
    samplePrompt: 'Tóm tắt nội dung quyết định giao đề tài trợ lý ảo cung cấp thông tin của Trung tâm R&D',
  },
  {
    id: 'doc-34',
    code: '1758/QĐ-TNVN',
    title: 'Định mức dự toán kinh phí nhiệm vụ KH&CN',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '28/05/2025',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Quy định định mức xây dựng dự toán kinh phí nhiệm vụ khoa học và công nghệ dùng ngân sách.',
    pages: 9,
    samplePrompt: 'Tóm tắt nội dung Quyết định 1758 về định mức dự toán kinh phí nhiệm vụ khoa học công nghệ',
  },
  {
    id: 'doc-35',
    code: '3398/QĐ-TNVN',
    title: 'Kế hoạch Ngày Chuyển đổi số quốc gia',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '09/10/2025',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Triển khai Quyết định 505/QĐ-TTg về Ngày Chuyển đổi số quốc gia tại Đài TNVN.',
    pages: 2,
    samplePrompt: 'Tóm tắt nội dung Quyết định 3398 về kế hoạch Ngày Chuyển đổi số quốc gia',
  },
  {
    id: 'doc-36',
    code: '2244/QĐ-TNVN',
    title: 'Điều chỉnh phương án phát sóng FM Cột 5 Hạ Long',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '09/07/2025',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Điều chỉnh phương án phát sóng FM tại trạm Cột 5 Hạ Long (Trung tâm Truyền thông Quảng Ninh).',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 2244 về phát sóng FM tại trạm Cột 5 Hạ Long',
  },
  {
    id: 'doc-37',
    code: '72/QĐ-TNVN',
    title: 'Phát sóng FM kênh VOV Giao thông Duyên Hải',
    category: 'tech',
    categoryLabel: 'Khoa học & Phát sóng',
    effectiveDate: '15/01/2026',
    authority: 'Tổng Giám đốc Đài TNVN',
    summary: 'Phát sóng FM kênh VOV Giao thông Duyên Hải tại trạm phát sóng Cột 5 Hạ Long.',
    pages: 1,
    samplePrompt: 'Tóm tắt nội dung Quyết định 72 về phát sóng FM kênh VOV Giao thông Duyên Hải',
  },
]

export const KnowledgeWorkspace: React.FC<KnowledgeWorkspaceProps> = ({ onSelectCitation }) => {
  const [searchQuery, setSearchQuery] = useState('')
  const [citations, setCitations] = useState<RAGCitation[]>([])
  const [answer, setAnswer] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedCategory, setSelectedCategory] = useState<string>('all')

  const handleSearch = async (queryToRun?: string) => {
    const q = queryToRun || searchQuery
    if (!q.trim() || isLoading) return

    setIsLoading(true)
    setError(null)
    setAnswer(null)

    try {
      const res = await apiClient.submitQuery(`Tìm trong tài liệu nội bộ: ${q}`)
      setAnswer(res.message)
      if (res.data?.citations && Array.isArray(res.data.citations)) {
        setCitations(res.data.citations as RAGCitation[])
      } else {
        setCitations([])
      }
    } catch (err: any) {
      setError(err?.message || 'Không thể tra cứu kho tri thức nội bộ.')
    } finally {
      setIsLoading(false)
    }
  }

  const handleResetSearch = () => {
    setSearchQuery('')
    setAnswer(null)
    setCitations([])
  }

  const filteredDocs = useMemo(() => {
    return INTERNAL_DOCUMENTS.filter((doc) => {
      const matchesCat = selectedCategory === 'all' || doc.category === selectedCategory
      const matchesSearch =
        !searchQuery.trim() ||
        doc.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        doc.code.toLowerCase().includes(searchQuery.toLowerCase()) ||
        doc.summary.toLowerCase().includes(searchQuery.toLowerCase())
      return matchesCat && matchesSearch
    })
  }, [selectedCategory, searchQuery])

  const sampleQueries = [
    'Quy chế làm việc của Đài TNVN',
    'Chương trình tiết kiệm, chống lãng phí 2026',
    'Quy chế văn thư, lưu trữ và ký số',
    'Tuyển dụng viên chức',
    'Danh hiệu Chiến sĩ thi đua cơ sở',
  ]

  const isShowingSearchResults = Boolean(answer || citations.length > 0)

  return (
    <div className="workspace-container knowledge-workspace">
      {/* Search Bar Hero Toolbar */}
      <div className="knowledge-search-hero">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleSearch()
          }}
          className="knowledge-search-form"
        >
          <div className="knowledge-input-wrap">
            <Search size={18} className="knowledge-search-icon" />
            <input
              type="text"
              className="knowledge-input"
              placeholder="Tra cứu quy chế, văn bản, quyết định hoặc tài liệu nội bộ..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                type="button"
                className="knowledge-clear-btn"
                onClick={() => setSearchQuery('')}
                title="Xóa tìm kiếm"
              >
                ✕
              </button>
            )}
          </div>
          <button
            type="submit"
            className="knowledge-submit-btn"
            disabled={!searchQuery.trim() || isLoading}
          >
            <Sparkles size={14} />
            <span>Tra cứu với AI</span>
          </button>
        </form>

        {/* Quick Sample Queries */}
        <div className="knowledge-sample-chips">
          <span className="sample-label">Gợi ý tra cứu:</span>
          {sampleQueries.map((query, i) => (
            <button
              key={i}
              className="sample-chip"
              onClick={() => {
                setSearchQuery(query)
                handleSearch(query)
              }}
            >
              {query}
            </button>
          ))}
        </div>
      </div>

      {/* Content Area */}
      <div className="workspace-content-scroll">
        {isLoading ? (
          <div className="workspace-loading-state">
            <RefreshCw size={20} className="spin-anim text-primary" />
            <span>Đang đối chiếu cơ sở dữ liệu RAG và trích xuất dẫn chứng...</span>
          </div>
        ) : error ? (
          <div className="workspace-error-card">
            <AlertCircle size={18} className="text-danger" />
            <div className="workspace-error-content">
              <h4>Lỗi tra cứu</h4>
              <p>{error}</p>
            </div>
            <button className="btn-retry" onClick={() => handleSearch()}>
              Thử lại
            </button>
          </div>
        ) : isShowingSearchResults ? (
          /* SEARCH RESULTS VIEW (AI Answer + Extracted Citations) */
          <div className="knowledge-results-layout">
            <div className="knowledge-back-bar">
              <button className="btn-back-to-catalog" onClick={handleResetSearch}>
                <ArrowLeft size={14} />
                <span>Quay lại danh mục văn bản ({INTERNAL_DOCUMENTS.length} tài liệu)</span>
              </button>
            </div>

            {/* AI Summarized Answer */}
            {answer && (
              <div className="knowledge-answer-card">
                <div className="answer-header">
                  <Sparkles size={16} className="text-primary" />
                  <h4>Tổng hợp nội dung từ quy chế & tài liệu nội bộ</h4>
                </div>
                <div className="answer-body">
                  <MarkdownContent content={answer} />
                </div>
              </div>
            )}

            {/* Citations Grid */}
            <div className="knowledge-citations-section">
              <h4 className="citations-section-title">
                Dẫn chứng trích xuất từ văn bản gốc ({citations.length})
              </h4>

              {citations.length === 0 ? (
                <p className="no-citations-hint">Không có đoạn trích dẫn cụ thể nào.</p>
              ) : (
                <div className="citations-grid">
                  {citations.map((cite, idx) => (
                    <div
                      key={idx}
                      className="knowledge-citation-card"
                      onClick={() => onSelectCitation(cite)}
                    >
                      <div className="citation-top">
                        <span className="citation-index">[{idx + 1}]</span>
                        <span className="citation-title">{cite.title}</span>
                        {cite.page_number && (
                          <span className="citation-page">Trang {cite.page_number}</span>
                        )}
                      </div>

                      {cite.section_title && (
                        <h5 className="citation-section">{cite.section_title}</h5>
                      )}

                      <p className="citation-snippet">{cite.text}</p>

                      <div className="citation-bottom">
                        <span className="citation-view-link">
                          <span>Xem chi tiết dẫn chứng</span>
                          <ExternalLink size={11} />
                        </span>
                        {cite.score && (
                          <span className="citation-score">
                            Khớp {Math.round(cite.score * 100)}%
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          /* DEFAULT VIEW: CORPORATE KNOWLEDGE CATALOG */
          <div className="knowledge-catalog-view">
            {/* Catalog Header & Category Tabs */}
            <div className="catalog-header-bar">
              <div className="catalog-title-wrap">
                <h3 className="catalog-heading">Danh mục Quyết định & Văn bản Đài TNVN</h3>
                <p className="catalog-subheading">
                  37 quyết định, chỉ thị đã được số hóa và ingest vào kho RAG — mọi thẻ đều tra cứu được.
                </p>
              </div>

              {/* Category Filter Pills */}
              <div className="catalog-category-pills">
                <button
                  className={`category-pill ${selectedCategory === 'all' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('all')}
                >
                  Tất cả ({INTERNAL_DOCUMENTS.length})
                </button>
                <button
                  className={`category-pill ${selectedCategory === 'policy' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('policy')}
                >
                  Quy chế & Chỉ đạo
                </button>
                <button
                  className={`category-pill ${selectedCategory === 'finance' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('finance')}
                >
                  Tài chính & Dự toán
                </button>
                <button
                  className={`category-pill ${selectedCategory === 'hr' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('hr')}
                >
                  Nhân sự & Đào tạo
                </button>
                <button
                  className={`category-pill ${selectedCategory === 'tech' ? 'category-pill--active' : ''}`}
                  onClick={() => setSelectedCategory('tech')}
                >
                  Khoa học & Phát sóng
                </button>
              </div>
            </div>

            {/* Document Cards Grid */}
            <div className="knowledge-docs-grid">
              {filteredDocs.map((doc) => (
                <div key={doc.id} className="knowledge-doc-item-card">
                  <div className="doc-card-header">
                    <span className="doc-code-badge">{doc.code}</span>
                    <span className="doc-category-badge">{doc.categoryLabel}</span>
                  </div>

                  <h4 className="doc-card-title">{doc.title}</h4>

                  <p className="doc-card-summary">{doc.summary}</p>

                  <div className="doc-card-meta">
                    <span className="meta-item">
                      <FileText size={12} />
                      <span>{doc.pages} trang</span>
                    </span>
                    <span className="meta-item">
                      <CheckCircle2 size={12} className="text-safe" />
                      <span>Hiệu lực: {doc.effectiveDate}</span>
                    </span>
                    <span className="meta-authority">{doc.authority}</span>
                  </div>

                  {/* Actions on Document Card */}
                  <div className="doc-card-actions">
                    <button
                      className="doc-action-btn doc-action-btn--primary"
                      onClick={() => {
                        setSearchQuery(doc.samplePrompt)
                        handleSearch(doc.samplePrompt)
                      }}
                      title="Yêu cầu Trợ lý AI tra cứu văn bản này"
                    >
                      <Sparkles size={13} />
                      <span>Tra cứu quy định</span>
                    </button>

                    <button
                      className="doc-action-btn doc-action-btn--secondary"
                      onClick={() => {
                        setSearchQuery(`Tóm tắt nội dung văn bản ${doc.title}`)
                        handleSearch(`Tóm tắt nội dung văn bản ${doc.title}`)
                      }}
                      title="Tóm tắt văn bản này"
                    >
                      <Eye size={13} />
                      <span>Tóm tắt</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
