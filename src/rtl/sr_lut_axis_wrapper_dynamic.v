`timescale 1ns/1ps

module sr_lut_axis_wrapper_dynamic #(
  parameter integer SCALE = 4,
  parameter integer SAMPLE_BITS = 5,
  parameter integer Q = 32,
  parameter integer LEVELS = 9,
  parameter integer LUT_DEPTH = 6561,
  parameter integer ADDR_BITS = 13,
  parameter integer MAX_FRAME_WIDTH = 480,
  parameter integer MAX_FRAME_HEIGHT = 320,
  parameter integer MIN_FRAME_WIDTH = 32,
  parameter integer MIN_FRAME_HEIGHT = 32,
  parameter integer SCHEDULER_CORES = 3,
  parameter MEM_FILE = ""
) (
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 ACLK CLK" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ACLK, ASSOCIATED_BUSIF S_AXIS:M_AXIS:S_AXI_CTRL, ASSOCIATED_RESET ARESETN, FREQ_HZ 100000000" *)
  input wire aclk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 ARESETN RST" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ARESETN, POLARITY ACTIVE_LOW" *)
  input wire aresetn,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1" *)
  input wire [31:0] s_axis_tdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TKEEP" *) input wire [3:0] s_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input wire s_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output wire s_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input wire s_axis_tlast,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output wire [31:0] m_axis_tdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *) output wire [3:0] m_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output wire m_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output wire m_axis_tlast,

  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL AWADDR" *) input wire [31:0] s_axi_ctrl_awaddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL AWVALID" *) input wire s_axi_ctrl_awvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL AWREADY" *) output wire s_axi_ctrl_awready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL WDATA" *) input wire [31:0] s_axi_ctrl_wdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL WSTRB" *) input wire [3:0] s_axi_ctrl_wstrb,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL WVALID" *) input wire s_axi_ctrl_wvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL WREADY" *) output wire s_axi_ctrl_wready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL BRESP" *) output wire [1:0] s_axi_ctrl_bresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL BVALID" *) output wire s_axi_ctrl_bvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL BREADY" *) input wire s_axi_ctrl_bready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL ARADDR" *) input wire [31:0] s_axi_ctrl_araddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL ARVALID" *) input wire s_axi_ctrl_arvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL ARREADY" *) output wire s_axi_ctrl_arready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL RDATA" *) output wire [31:0] s_axi_ctrl_rdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL RRESP" *) output wire [1:0] s_axi_ctrl_rresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL RVALID" *) output wire s_axi_ctrl_rvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI_CTRL RREADY" *) input wire s_axi_ctrl_rready,

  output wire [31:0] perf_frame_cycles,
  output wire [31:0] perf_input_backpressure_cycles,
  output wire [31:0] perf_output_backpressure_cycles,
  output wire [31:0] perf_input_starvation_cycles
);

  localparam [31:0] ERR_WIDTH_LOW = 32'h00000001;
  localparam [31:0] ERR_WIDTH_HIGH = 32'h00000002;
  localparam [31:0] ERR_HEIGHT_LOW = 32'h00000004;
  localparam [31:0] ERR_HEIGHT_HIGH = 32'h00000008;
  localparam [31:0] ERR_START_BUSY = 32'h00000010;
  localparam [31:0] ERR_CONFIG_BUSY = 32'h00000020;
  localparam [31:0] ERR_INPUT_TLAST = 32'h00000040;
  localparam [31:0] ERR_INPUT_OVERFLOW = 32'h00000080;
  localparam [31:0] ERR_OUTPUT_PROTOCOL = 32'h00000100;

  reg [31:0] cfg_width_reg, cfg_height_reg;
  reg [9:0] active_width_reg, active_height_reg;
  reg [31:0] expected_input_words_reg, expected_output_words_reg;
  reg [31:0] input_count_reg, output_count_reg;
  reg [31:0] error_code_reg;
  reg running_reg, done_reg, error_reg, soft_reset_active_reg, frame_start_reg;
  reg aw_pending, w_pending;
  reg [5:0] awaddr_reg;
  reg [31:0] wdata_reg;
  reg [3:0] wstrb_reg;
  reg bvalid_reg, rvalid_reg;
  reg [31:0] rdata_hold;
  reg [31:0] read_mux;

  wire core_resetn = aresetn && !soft_reset_active_reg;
  wire core_s_ready;
  wire [23:0] core_m_data;
  wire core_m_valid;
  wire core_m_last;
  wire input_fire = s_axis_tvalid && s_axis_tready;
  wire output_fire = m_axis_tvalid && m_axis_tready;
  wire write_commit = aw_pending && w_pending && !bvalid_reg;
  wire [19:0] configured_input_words =
      cfg_width_reg[9:0] * cfg_height_reg[9:0];

  assign s_axi_ctrl_awready = !aw_pending && !bvalid_reg;
  assign s_axi_ctrl_wready = !w_pending && !bvalid_reg;
  assign s_axi_ctrl_bresp = 2'b00;
  assign s_axi_ctrl_bvalid = bvalid_reg;
  assign s_axi_ctrl_arready = !rvalid_reg;
  assign s_axi_ctrl_rvalid = rvalid_reg;
  assign s_axi_ctrl_rresp = 2'b00;
  assign s_axi_ctrl_rdata = rdata_hold;

  assign s_axis_tready = running_reg && !error_reg && core_s_ready;
  assign m_axis_tvalid = running_reg && !error_reg && core_m_valid;
  assign m_axis_tlast = m_axis_tvalid && core_m_last;
  assign m_axis_tdata = {8'b0, core_m_data};
  assign m_axis_tkeep = 4'b1111;

  sr_lut_axis_top_dynamic #(
    .SCALE(SCALE), .SAMPLE_BITS(SAMPLE_BITS), .Q(Q), .LEVELS(LEVELS),
    .LUT_DEPTH(LUT_DEPTH), .ADDR_BITS(ADDR_BITS),
    .MAX_FRAME_WIDTH(MAX_FRAME_WIDTH), .MAX_FRAME_HEIGHT(MAX_FRAME_HEIGHT),
    .SCHEDULER_CORES(SCHEDULER_CORES), .MEM_FILE(MEM_FILE)
  ) core (
    .aclk(aclk), .aresetn(core_resetn),
    .s_axis_tdata_i(s_axis_tdata[23:0]),
    .s_axis_tvalid_i(s_axis_tvalid && running_reg && !error_reg),
    .s_axis_tready_o(core_s_ready), .s_axis_tlast_i(s_axis_tlast),
    .active_width_i(active_width_reg), .active_height_i(active_height_reg),
    .frame_start_i(frame_start_reg),
    .m_axis_tdata_o(core_m_data), .m_axis_tvalid_o(core_m_valid),
    .m_axis_tready_i(m_axis_tready && running_reg && !error_reg),
    .m_axis_tlast_o(core_m_last)
  );

  sr_lut_perf_monitor perf_monitor (
    .clk_i(aclk), .resetn_i(core_resetn),
    .s_axis_tvalid_i(s_axis_tvalid), .s_axis_tready_i(s_axis_tready),
    .s_axis_tlast_i(s_axis_tlast), .m_axis_tvalid_i(m_axis_tvalid),
    .m_axis_tready_i(m_axis_tready), .m_axis_tlast_i(m_axis_tlast),
    .frame_cycles_o(perf_frame_cycles),
    .input_backpressure_cycles_o(perf_input_backpressure_cycles),
    .output_backpressure_cycles_o(perf_output_backpressure_cycles),
    .input_starvation_cycles_o(perf_input_starvation_cycles)
  );

  always @* begin
    read_mux = 32'b0;
    case (s_axi_ctrl_araddr[5:0])
      6'h00: read_mux = {30'b0, error_reg, running_reg};
      6'h04: read_mux = cfg_width_reg;
      6'h08: read_mux = cfg_height_reg;
      6'h0c: read_mux = {28'b0, error_reg, done_reg, running_reg, !running_reg && !error_reg};
      6'h10: read_mux = expected_input_words_reg;
      6'h14: read_mux = expected_output_words_reg;
      6'h18: read_mux = error_code_reg;
      6'h20: read_mux = perf_frame_cycles;
      6'h24: read_mux = perf_input_backpressure_cycles;
      6'h28: read_mux = perf_output_backpressure_cycles;
      6'h2c: read_mux = perf_input_starvation_cycles;
      6'h34: read_mux = SCALE;
      default: read_mux = 32'b0;
    endcase
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      cfg_width_reg <= 32'd320;
      cfg_height_reg <= 32'd180;
      active_width_reg <= 10'd320;
      active_height_reg <= 10'd180;
      expected_input_words_reg <= 32'd57600;
      expected_output_words_reg <= 32'd57600 * SCALE * SCALE;
      input_count_reg <= 0;
      output_count_reg <= 0;
      error_code_reg <= 0;
      running_reg <= 1'b0;
      done_reg <= 1'b0;
      error_reg <= 1'b0;
      soft_reset_active_reg <= 1'b0;
      frame_start_reg <= 1'b0;
      aw_pending <= 1'b0;
      w_pending <= 1'b0;
      awaddr_reg <= 0;
      wdata_reg <= 0;
      wstrb_reg <= 0;
      bvalid_reg <= 1'b0;
      rvalid_reg <= 1'b0;
      rdata_hold <= 0;
    end else begin
      frame_start_reg <= 1'b0;
      if (soft_reset_active_reg) begin
        soft_reset_active_reg <= 1'b0;
        running_reg <= 1'b0;
        done_reg <= 1'b0;
        error_reg <= 1'b0;
        error_code_reg <= 0;
        input_count_reg <= 0;
        output_count_reg <= 0;
      end

      if (s_axi_ctrl_awvalid && s_axi_ctrl_awready) begin
        aw_pending <= 1'b1;
        awaddr_reg <= s_axi_ctrl_awaddr;
      end
      if (s_axi_ctrl_wvalid && s_axi_ctrl_wready) begin
        w_pending <= 1'b1;
        wdata_reg <= s_axi_ctrl_wdata;
        wstrb_reg <= s_axi_ctrl_wstrb;
      end
      if (write_commit) begin
        aw_pending <= 1'b0;
        w_pending <= 1'b0;
        bvalid_reg <= 1'b1;
        if (wstrb_reg[0]) begin
          case (awaddr_reg)
            6'h00: begin
              if (wdata_reg[1]) begin
                soft_reset_active_reg <= 1'b1;
              end else if (wdata_reg[0]) begin
                if (running_reg) begin
                  error_reg <= 1'b1;
                  error_code_reg <= ERR_START_BUSY;
                  running_reg <= 1'b0;
                end else if ((cfg_width_reg < MIN_FRAME_WIDTH)
                             || (cfg_width_reg > MAX_FRAME_WIDTH)
                             || (cfg_height_reg < MIN_FRAME_HEIGHT)
                             || (cfg_height_reg > MAX_FRAME_HEIGHT)) begin
                  error_reg <= 1'b1;
                  error_code_reg <= ((cfg_width_reg < MIN_FRAME_WIDTH) ? ERR_WIDTH_LOW : 0)
                      | ((cfg_width_reg > MAX_FRAME_WIDTH) ? ERR_WIDTH_HIGH : 0)
                      | ((cfg_height_reg < MIN_FRAME_HEIGHT) ? ERR_HEIGHT_LOW : 0)
                      | ((cfg_height_reg > MAX_FRAME_HEIGHT) ? ERR_HEIGHT_HIGH : 0);
                end else begin
                  active_width_reg <= cfg_width_reg[9:0];
                  active_height_reg <= cfg_height_reg[9:0];
                  expected_input_words_reg <= configured_input_words;
                  expected_output_words_reg <=
                      {12'b0, configured_input_words} * SCALE * SCALE;
                  input_count_reg <= 0;
                  output_count_reg <= 0;
                  done_reg <= 1'b0;
                  error_reg <= 1'b0;
                  error_code_reg <= 0;
                  running_reg <= 1'b1;
                  frame_start_reg <= 1'b1;
                end
              end
            end
            6'h04: begin
              if (running_reg) begin error_reg <= 1'b1; error_code_reg <= ERR_CONFIG_BUSY; running_reg <= 1'b0; end
              else cfg_width_reg <= wdata_reg;
            end
            6'h08: begin
              if (running_reg) begin error_reg <= 1'b1; error_code_reg <= ERR_CONFIG_BUSY; running_reg <= 1'b0; end
              else cfg_height_reg <= wdata_reg;
            end
            default: begin end
          endcase
        end
      end
      if (bvalid_reg && s_axi_ctrl_bready) begin bvalid_reg <= 1'b0; end
      if (s_axi_ctrl_arvalid && s_axi_ctrl_arready) begin
        rdata_hold <= read_mux;
        rvalid_reg <= 1'b1;
      end
      if (rvalid_reg && s_axi_ctrl_rready) begin rvalid_reg <= 1'b0; end

      if (input_fire) begin
        if (input_count_reg >= expected_input_words_reg
            || (s_axis_tlast && input_count_reg != expected_input_words_reg - 1)) begin
          error_reg <= 1'b1; error_code_reg <= s_axis_tlast ? ERR_INPUT_TLAST : ERR_INPUT_OVERFLOW; running_reg <= 1'b0;
        end else if (input_count_reg == expected_input_words_reg - 1) begin
          input_count_reg <= expected_input_words_reg;
          if (!s_axis_tlast) begin error_reg <= 1'b1; error_code_reg <= ERR_INPUT_TLAST; running_reg <= 1'b0; end
        end else begin
          input_count_reg <= input_count_reg + 1;
        end
      end
      if (output_fire) begin
        if (output_count_reg >= expected_output_words_reg
            || (m_axis_tlast && output_count_reg != expected_output_words_reg - 1)) begin
          error_reg <= 1'b1; error_code_reg <= ERR_OUTPUT_PROTOCOL; running_reg <= 1'b0;
        end else if (output_count_reg == expected_output_words_reg - 1) begin
          output_count_reg <= expected_output_words_reg;
          if (!m_axis_tlast) begin error_reg <= 1'b1; error_code_reg <= ERR_OUTPUT_PROTOCOL; running_reg <= 1'b0; end
          else begin running_reg <= 1'b0; done_reg <= 1'b1; end
        end else begin
          output_count_reg <= output_count_reg + 1;
        end
      end
    end
  end

`ifndef SYNTHESIS
  always @(posedge aclk) begin
    if (aresetn && s_axis_tvalid && s_axis_tready && s_axis_tkeep !== 4'b1111)
      $fatal(1, "dynamic SR-LUT requires one uint32 word per pixel");
  end
`endif

endmodule
