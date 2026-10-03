import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import SourcesPanel from "../SourcesPanel";

describe("SourcesPanel", () => {
  it("爬蟲放在來源資料夾裡的文件，連結保留斜線、每一段各自編碼", () => {
    render(<SourcesPanel sources={["教務處註冊組/1-10 學生證遺失補發－說明.pdf"]} />);

    const link = screen.getByRole("link", { name: /學生證遺失補發/ });
    expect(link).toHaveAttribute(
      "href",
      `/files/${encodeURIComponent("教務處註冊組")}/${encodeURIComponent("1-10 學生證遺失補發－說明.pdf")}`,
    );
  });

  it("沒有來源時不顯示", () => {
    const { container } = render(<SourcesPanel sources={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
