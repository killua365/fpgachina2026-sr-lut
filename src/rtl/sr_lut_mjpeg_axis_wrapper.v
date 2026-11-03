`timescale 1ns/1ps

module sr_lut_mjpeg_axis_wrapper #(
  parameter integer IMG_WIDTH = 1920,
  parameter integer IMG_HEIGHT = 1080,
  parameter integer QUALITY = 80
) (
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 ACLK CLK" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ACLK, ASSOCIATED_BUSIF S_AXIS:M_AXIS, ASSOCIATED_RESET ARESETN, FREQ_HZ 150000000" *)
  input wire aclk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 ARESETN RST" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ARESETN, POLARITY ACTIVE_LOW" *)
  input wire aresetn,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 2, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1, HAS_TUSER 1, TUSER_WIDTH 1" *)
  input wire [15:0] s_axis_tdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TKEEP" *) input wire [1:0] s_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input wire s_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output wire s_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input wire s_axis_tlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TUSER" *) input wire s_axis_tuser,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output wire [7:0] m_axis_tdata,
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS, TDATA_NUM_BYTES 1, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1" *)
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *) output wire [0:0] m_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output wire m_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output wire m_axis_tlast,

  output reg jpeg_overflow
);

  wire ctrl_awready, ctrl_wready, ctrl_bvalid, unused_arready;
  wire [1:0] ctrl_bresp, unused_rresp;
  wire [31:0] unused_rdata;
  wire unused_rvalid, encoder_input_ready;
  reg ctrl_awvalid, ctrl_wvalid, encoder_enabled;

  assign s_axis_tready = encoder_enabled && encoder_input_ready;

  assign m_axis_tkeep = 1'b1;

  always @(posedge aclk) begin
    if (!aresetn) begin
      jpeg_overflow <= 1'b0;
      ctrl_awvalid <= 1'b1;
      ctrl_wvalid <= 1'b1;
      encoder_enabled <= 1'b0;
    end else begin
      if (ctrl_awvalid && ctrl_awready)
        ctrl_awvalid <= 1'b0;
      if (ctrl_wvalid && ctrl_wready)
        ctrl_wvalid <= 1'b0;
      if (ctrl_bvalid && ctrl_bresp == 2'b00)
        encoder_enabled <= 1'b1;
      if (m_axis_tvalid && !m_axis_tready)
        jpeg_overflow <= 1'b1;
    end
  end

  mjpegzero_enc_top #(
    .LITE_MODE(1),
    .LITE_QUALITY(QUALITY),
    .IMG_WIDTH(IMG_WIDTH),
    .IMG_HEIGHT(IMG_HEIGHT),
    .RGB_INPUT(0)
  ) encoder (
    .clk(aclk),
    .rst_n(aresetn),
    .s_axis_vid_tdata(s_axis_tdata),
    .s_axis_vid_tvalid(s_axis_tvalid && encoder_enabled &&
                       (&s_axis_tkeep)),
    .s_axis_vid_tready(encoder_input_ready),
    .s_axis_vid_tlast(s_axis_tlast),
    .s_axis_vid_tuser(s_axis_tuser),
    .m_axis_jpg_tvalid(m_axis_tvalid),
    .m_axis_jpg_tdata(m_axis_tdata),
    .m_axis_jpg_tlast(m_axis_tlast),
    .s_axi_awaddr(5'h00),
    .s_axi_awvalid(ctrl_awvalid),
    .s_axi_awready(ctrl_awready),
    .s_axi_wdata(32'h00000001),
    .s_axi_wstrb(4'b1111),
    .s_axi_wvalid(ctrl_wvalid),
    .s_axi_wready(ctrl_wready),
    .s_axi_bresp(ctrl_bresp),
    .s_axi_bvalid(ctrl_bvalid),
    .s_axi_bready(1'b1),
    .s_axi_araddr(5'b0),
    .s_axi_arvalid(1'b0),
    .s_axi_arready(unused_arready),
    .s_axi_rdata(unused_rdata),
    .s_axi_rresp(unused_rresp),
    .s_axi_rvalid(unused_rvalid),
    .s_axi_rready(1'b1)
  );

endmodule
