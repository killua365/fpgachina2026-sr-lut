`timescale 1ns/1ps

module sr_lut_jpeg_packer_async #(
  parameter integer FIFO_DEPTH_WORDS = 256
) (
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 S_CLK CLK" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_CLK, ASSOCIATED_BUSIF S_AXIS, ASSOCIATED_RESET S_RESETN, FREQ_HZ 150000000" *)
  input wire s_clk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 S_RESETN RST" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_RESETN, POLARITY ACTIVE_LOW" *)
  input wire s_resetn,
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 M_CLK CLK" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_CLK, ASSOCIATED_BUSIF M_AXIS, ASSOCIATED_RESET M_RESETN, FREQ_HZ 100000000" *)
  input wire m_clk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 M_RESETN RST" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_RESETN, POLARITY ACTIVE_LOW" *)
  input wire m_resetn,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 1, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1" *)
  input wire [7:0] s_axis_tdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TKEEP" *) input wire [0:0] s_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input wire s_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output wire s_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input wire s_axis_tlast,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output wire [31:0] m_axis_tdata,
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1" *)
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *) output wire [3:0] m_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output wire m_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output wire m_axis_tlast,

  output reg jpeg_overflow,
  output reg [31:0] jpeg_byte_count
);

  reg [31:0] pack_data_reg;
  reg [1:0] pack_count_reg;
  reg [36:0] fifo_in_reg;
  reg fifo_write_reg;
  reg jpeg_frame_done;
  wire [36:0] fifo_out;
  wire fifo_full, fifo_empty, wr_rst_busy, rd_rst_busy;
  // XPM handles reset release independently in both clock domains. Drive its
  // asynchronous reset from the write-domain reset so reset deassertion is
  // not timed as a combinational 100 MHz to 150 MHz data path.
  wire fifo_reset = !s_resetn;
  wire flush_word = pack_count_reg == 2'd3 || s_axis_tlast;

  // The encoder has no backpressure. This signal reports capacity so the
  // wrapper can raise a sticky overflow flag if the FIFO ever fills.
  assign s_axis_tready = !fifo_full && !wr_rst_busy;
  assign m_axis_tvalid = m_resetn && !fifo_empty && !rd_rst_busy;
  assign {m_axis_tlast, m_axis_tkeep, m_axis_tdata} = fifo_out;

  always @(posedge m_clk) begin
    if (!m_resetn) begin
      jpeg_byte_count <= 32'b0;
      jpeg_frame_done <= 1'b0;
    end else if (m_axis_tvalid && m_axis_tready) begin
      // TLAST terminates the current JPEG. Preserve its final length until
      // the next frame starts, then begin that frame's count at this word.
      if (jpeg_frame_done)
        jpeg_byte_count <= (m_axis_tkeep[0] + m_axis_tkeep[1] +
                            m_axis_tkeep[2] + m_axis_tkeep[3]);
      else
        jpeg_byte_count <= jpeg_byte_count +
                           (m_axis_tkeep[0] + m_axis_tkeep[1] +
                            m_axis_tkeep[2] + m_axis_tkeep[3]);
      jpeg_frame_done <= m_axis_tlast;
    end
  end

  always @(posedge s_clk) begin
    if (!s_resetn) begin
      pack_data_reg <= 0;
      pack_count_reg <= 0;
      fifo_in_reg <= 0;
      fifo_write_reg <= 1'b0;
      jpeg_overflow <= 1'b0;
    end else begin
      fifo_write_reg <= 1'b0;
      if (s_axis_tvalid && s_axis_tkeep[0]) begin
        if (!s_axis_tready) begin
          jpeg_overflow <= 1'b1;
          pack_count_reg <= 0;
        end else if (flush_word) begin
          case (pack_count_reg)
            2'd0: fifo_in_reg <= {s_axis_tlast, 4'b0001,
                                  24'b0, s_axis_tdata};
            2'd1: fifo_in_reg <= {s_axis_tlast, 4'b0011,
                                  16'b0, s_axis_tdata, pack_data_reg[7:0]};
            2'd2: fifo_in_reg <= {s_axis_tlast, 4'b0111,
                                  8'b0, s_axis_tdata, pack_data_reg[15:0]};
            default: fifo_in_reg <= {s_axis_tlast, 4'b1111,
                                     s_axis_tdata, pack_data_reg[23:0]};
          endcase
          fifo_write_reg <= 1'b1;
          pack_count_reg <= 0;
          pack_data_reg <= 0;
        end else begin
          case (pack_count_reg)
            2'd0: pack_data_reg[7:0] <= s_axis_tdata;
            2'd1: pack_data_reg[15:8] <= s_axis_tdata;
            default: pack_data_reg[23:16] <= s_axis_tdata;
          endcase
          pack_count_reg <= pack_count_reg + 1'b1;
        end
      end
    end
  end

  xpm_fifo_async #(
    .CDC_SYNC_STAGES(2),
    .DOUT_RESET_VALUE("0"),
    .ECC_MODE("no_ecc"),
    .FIFO_MEMORY_TYPE("block"),
    .FIFO_READ_LATENCY(0),
    .FIFO_WRITE_DEPTH(FIFO_DEPTH_WORDS),
    .FULL_RESET_VALUE(0),
    .PROG_EMPTY_THRESH(10),
    .PROG_FULL_THRESH(10),
    .RD_DATA_COUNT_WIDTH($clog2(FIFO_DEPTH_WORDS) + 1),
    .READ_DATA_WIDTH(37),
    .READ_MODE("fwft"),
    .RELATED_CLOCKS(0),
    .SIM_ASSERT_CHK(1),
    .USE_ADV_FEATURES("0000"),
    .WAKEUP_TIME(0),
    .WRITE_DATA_WIDTH(37),
    .WR_DATA_COUNT_WIDTH($clog2(FIFO_DEPTH_WORDS) + 1)
  ) fifo (
    .rst(fifo_reset),
    .wr_clk(s_clk),
    .wr_en(fifo_write_reg),
    .din(fifo_in_reg),
    .full(fifo_full),
    .wr_rst_busy(wr_rst_busy),
    .rd_clk(m_clk),
    .rd_en(m_axis_tvalid && m_axis_tready),
    .dout(fifo_out),
    .empty(fifo_empty),
    .rd_rst_busy(rd_rst_busy),
    .sleep(1'b0),
    .injectsbiterr(1'b0),
    .injectdbiterr(1'b0)
  );

endmodule
