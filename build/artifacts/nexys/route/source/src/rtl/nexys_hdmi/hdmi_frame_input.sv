// UI-domain frame adapter. Tokens: {end,good,eol,sof,RGBX[31:0]}.
// Once granted, a frame always supplies the full VDMA extent. A damaged input
// is padded only for draining the existing AXI write; it is never publishable.
// Backpressure cannot alter a pending AXI beat, even on loss/abort.
module hdmi_frame_input #(
    parameter integer WIDTH=1920,HEIGHT=1080,STALL_CYCLES=200000
)(
    input wire clk,resetn,source_present,discontinuity,
    input wire [35:0] token,input wire token_valid,output reg token_pop,
    output wire offer,input wire accept,start,
    output reg done=0,good=0,active=0,output reg [31:0] frame_crc=0,
    output reg [31:0] accepted=0,rejected=0,damaged=0,
    output reg [31:0] tdata=0,output wire [3:0] tkeep,
    output reg tvalid=0,tuser=0,tlast=0,input wire tready
);
    localparam SEARCH=0,ARM=1,STREAM=2,TAIL=3,DISCARD=4;
    `include "frame_crc32.vh"
    reg [31:0] crc=32'hffffffff;
    reg [31:0] next_crc;
    always @* begin
        next_crc=crc;
        for(integer lane=0;lane<4;lane=lane+1)next_crc=frame_crc_byte(next_crc,tdata[lane*8+:8]);
    end
    reg [2:0] state=SEARCH;
    reg bad=0,ended=0,pad=0,output_done=0,arm_initialized=0;
    reg [31:0] issued=0,x=0,stall=0;
    wire room=!tvalid||tready;
    wire timeout=active&&stall==STALL_CYCLES-1;
    wire abort_now=active&&(!source_present||discontinuity||timeout);
    wire first=issued==0,last=issued==WIDTH*HEIGHT-1;
    wire token_bad=token[35]||token[32]!=first||token[33]!=(x==WIDTH-1);
    assign offer=state==SEARCH&&token_valid&&token[32]&&!token[35]&&source_present&&!discontinuity;
    assign tkeep=4'hf;
    always @* begin
        token_pop=0;
        // Holding SOF does not depend combinationally on the owner's grant.
        // A rejected SOF is consumed in DISCARD on the next cycle, removing
        // the owner epoch -> grant -> FIFO BRAM-enable timing path.
        if(state==SEARCH)token_pop=token_valid&&!offer;
        else if(state==DISCARD)token_pop=token_valid;
        else if(state==STREAM&&room&&issued<WIDTH*HEIGHT&&!pad&&!abort_now)
            token_pop=token_valid;
        else if(state==TAIL&&!ended)token_pop=token_valid&&!token[32];
    end
    always @(posedge clk)begin
        done<=0;
        if(!resetn)begin
            state<=SEARCH;active<=0;bad<=0;ended<=0;pad<=0;output_done<=0;arm_initialized<=0;
            issued<=0;x<=0;stall<=0;tvalid<=0;tdata<=0;tuser<=0;tlast<=0;good<=0;
            accepted<=0;rejected<=0;damaged<=0;crc<=32'hffffffff;frame_crc<=0;
        end else begin
            if(state==DISCARD||(state==SEARCH&&token_pop&&token[32]))rejected<=rejected+1'b1;
            if(active)begin
                if(token_pop)stall<=0;
                else if(stall<STALL_CYCLES-1)stall<=stall+1'b1;
            end else stall<=0;
            if(tvalid&&tready)begin
                tvalid<=0;
                crc<=next_crc;
                if(issued==WIDTH*HEIGHT)begin output_done<=1;frame_crc<=~next_crc;end
            end
            if(abort_now)begin bad<=1;pad<=1;ended<=1;end
            case(state)
                SEARCH:begin
                    if(offer&&accept)begin
                        active<=1;state<=ARM;arm_initialized<=0;
                    end else if(offer)state<=DISCARD;
                end
                DISCARD:state<=SEARCH;
                // Owner grant only changes the small state register. Initialize
                // wide counters in the registered ARM phase, once per frame.
                // Capture a simultaneous loss instead of erasing its abort.
                ARM:begin
                    if(!arm_initialized)begin
                        arm_initialized<=1;accepted<=accepted+1'b1;
                        issued<=0;x<=0;stall<=0;crc<=32'hffffffff;
                        bad<=abort_now;pad<=abort_now;ended<=abort_now;output_done<=0;good<=0;
                    end
                    if(start)state<=STREAM;
                end
                STREAM:if(room&&issued<WIDTH*HEIGHT)begin
                    if(pad||abort_now)begin
                        tvalid<=1;tdata<=0;tuser<=first;tlast<=x==WIDTH-1;
                        issued<=issued+1'b1;x<=x==WIDTH-1?0:x+1'b1;
                        if(last)state<=TAIL;
                    end else if(token_valid)begin
                        if(token_bad)begin
                            bad<=1;pad<=1;
                            // An early end is consumed. A new SOF means the
                            // original frame disappeared; this newer frame is
                            // deliberately dropped rather than interleaved.
                            if(token[35]||token[32]&&!first)ended<=1;
                        end else begin
                            tvalid<=1;tdata<=token[31:0];tuser<=first;tlast<=x==WIDTH-1;
                            issued<=issued+1'b1;x<=x==WIDTH-1?0:x+1'b1;
                            if(last)state<=TAIL;
                        end
                    end
                end
                TAIL:begin
                    if(token_valid&&!ended)begin
                        if(token[35])begin ended<=1;if(!token[34])bad<=1;end
                        else if(token[32])begin ended<=1;bad<=1;end
                        else bad<=1; // more pixels than the configured extent
                    end
                    if(output_done&&ended)begin
                        good<=!bad&&!abort_now;done<=1;active<=0;state<=SEARCH;
                        if(bad||abort_now)damaged<=damaged+1'b1;
                    end
                end
            endcase
        end
    end
endmodule
